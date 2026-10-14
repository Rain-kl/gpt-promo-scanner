"""Own the worker process and apply its structured events to durable state."""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import uuid

from promo_dashboard.storage import ACTIVE_STATES, ROOT, now
from promo_dashboard.worker import build_plan


class JobManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.processes = {}
        self.threads = {}
        for job in store.jobs():
            if job["status"] in ACTIVE_STATES:
                store.update_job(
                    job["id"],
                    status="interrupted",
                    ended_at=now(),
                    error="面板服务已重新启动，任务已中断；已有结果保留",
                )

    @staticmethod
    def redact(value, settings):
        if isinstance(value, str):
            for secret in (settings.token, settings.clash_secret):
                if secret:
                    value = value.replace(secret, "[已隐藏凭据]")
            return re.sub(
                r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[已隐藏凭据]", value
            )
        if isinstance(value, dict):
            return {key: JobManager.redact(item, settings) for key, item in value.items()}
        if isinstance(value, list):
            return [JobManager.redact(item, settings) for item in value]
        return value

    def start(self, request, settings):
        with self.lock:
            if any(job["status"] in ACTIVE_STATES for job in self.store.jobs()):
                raise ValueError("已有任务正在运行，请等待完成或停止当前任务")
            plan = build_plan(request, settings)
            if not plan:
                if request.mode == "scan":
                    raise ValueError("当前地区没有内置已知码，请添加自定义候选码，或改用候选发现")
                raise ValueError("候选列表为空，请启用内置公司名或添加自定义候选码")
            if not request.preview and not settings.token:
                raise ValueError("请先在「连接设置」中保存 ChatGPT Access Token，或使用预览候选")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "request": request.model_dump(),
                "status": "starting",
                "created_at": now(),
                "started_at": now(),
                "ended_at": None,
                "done": 0,
                "total": len(plan),
                "current": "",
                "error": None,
                "counts": dict.fromkeys(
                    ("eligible", "exists", "not_found", "error", "skipped", "preview"), 0
                ),
            }
            run_dir = self.store.directory / "runs" / job_id
            run_dir.mkdir(parents=True, mode=0o700)
            control_file = run_dir / "control.json"
            control_file.write_text('{"action":"run"}', encoding="utf-8")
            self.store.save_job(job)
            try:
                process = subprocess.Popen(
                    [sys.executable, "-u", "-m", "promo_dashboard.worker"],
                    cwd=ROOT,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    start_new_session=os.name != "nt",
                )
                self.processes[job_id] = process
                process.stdin.write(
                    json.dumps(
                        {
                            "request": request.model_dump(),
                            "settings": settings.model_dump(),
                            "control_file": str(control_file),
                        }
                    )
                )
                process.stdin.close()
            except Exception as error:
                self.store.update_job(
                    job_id, status="failed", ended_at=now(), error="无法启动执行进程"
                )
                raise RuntimeError("无法启动执行进程") from error
            thread = threading.Thread(
                target=self._monitor, args=(job_id, process, settings), daemon=True
            )
            self.threads[job_id] = thread
            thread.start()
            return job

    def _monitor(self, job_id, process, settings):
        try:
            for line in process.stdout:
                try:
                    event = self.redact(json.loads(line), settings)
                except (ValueError, TypeError):
                    self.store.log(job_id, self.redact(line.strip(), settings))
                    continue
                kind = event.get("type")
                with self.lock:
                    job = self.store.job(job_id)
                    if kind == "log":
                        self.store.log(job_id, event["message"])
                    elif kind == "result":
                        result = event["result"]
                        self.store.add_result(job_id, result)
                        counts = job["counts"]
                        key = result["status"].lower()
                        counts[key] = counts.get(key, 0) + 1
                        self.store.update_job(job_id, counts=counts)
                        self.store.log(
                            job_id,
                            f"{result['region']} · {result['code']} · "
                            f"{result['status']}"
                            + (f" · {result['detail']}" if result.get("detail") else ""),
                        )
                    elif kind == "progress":
                        self.store.update_job(
                            job_id,
                            done=event["done"],
                            total=event["total"],
                            current=event["current"],
                        )
                    elif kind == "state":
                        if job["status"] != "stopping" and event["status"] != "cancelled":
                            # A late worker startup event must not erase a pause request.
                            if not (job["status"] == "pausing" and event["status"] == "running"):
                                self.store.update_job(job_id, status=event["status"])
                    elif kind == "error":
                        self.store.log(job_id, event["message"])
                        # Terminal states are published atomically with ended_at after process exit.
                        self.store.update_job(job_id, error=event["message"])
            code = process.wait()
            with self.lock:
                job = self.store.job(job_id)
                if job["status"] == "stopping" or code == 130:
                    status = "cancelled"
                elif code != 0 or job["error"]:
                    status = "failed"
                else:
                    status = "completed"
                self.store.update_job(
                    job_id,
                    status=status,
                    ended_at=now(),
                    exit_code=code,
                    error=job["error"] or ("执行进程意外退出" if status == "failed" else None),
                )
        except Exception as error:
            self.store.update_job(
                job_id, status="failed", ended_at=now(), error=self.redact(str(error), settings)
            )
            if process.poll() is None:
                process.terminate()
        finally:
            process.stdout.close()
            with self.lock:
                self.processes.pop(job_id, None)
                self.threads.pop(job_id, None)

    def control(self, job_id, action):
        with self.lock:
            job = self.store.job(job_id)
            process = self.processes.get(job_id)
            if not job or job["status"] not in ACTIVE_STATES or not process:
                raise ValueError("此任务已经结束，无法继续控制")
            if action == "pause" and job["status"] not in ("starting", "running"):
                raise ValueError("只有运行中的任务可以暂停")
            if action == "resume" and job["status"] not in ("pausing", "paused"):
                raise ValueError("只有暂停中的任务可以继续")
            if job["status"] == "stopping":
                raise ValueError("任务正在停止，请等待退出")
            control_file = self.store.directory / "runs" / job_id / "control.json"
            temporary = control_file.with_suffix(".tmp")
            temporary.write_text(json.dumps({"action": "run" if action == "resume" else action}))
            temporary.replace(control_file)
            state = {"pause": "pausing", "resume": "running", "stop": "stopping"}[action]
            self.store.update_job(job_id, status=state)
            if action == "stop":
                # Interrupt network calls, then allow a bounded window for cleanup/node restoration.
                process.terminate()
                threading.Thread(target=self._force_stop, args=(process,), daemon=True).start()
            return self.store.job(job_id)

    @staticmethod
    def _force_stop(process):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def shutdown(self):
        for job_id in list(self.processes):
            try:
                self.control(job_id, "stop")
            except ValueError:
                pass
        for thread in list(self.threads.values()):
            thread.join(timeout=6)

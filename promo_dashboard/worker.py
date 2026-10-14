"""Structured, cancellable scanner execution in a separate process.

Credentials arrive through stdin, never command-line arguments or job result files.
"""

import contextlib
import io
import json
import signal
import sys
import time
from pathlib import Path

from promo_dashboard.models import JobRequest, Settings


class Cancelled(Exception):
    pass


def emit(kind, **data):
    sys.__stdout__.write(json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


class LogWriter(io.TextIOBase):
    def __init__(self):
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                emit("log", message=line)
        return len(text)

    def flush(self):
        if self.buffer.strip():
            emit("log", message=self.buffer)
            self.buffer = ""


def build_plan(request: JobRequest, settings: Settings):
    """Generate a bounded, deterministic plan without network or file changes."""
    import auto_scan as scan
    import discover_codes as discovery

    regions = list(discovery.COUNTRIES) if request.region == "ALL" else [request.region]
    rows = []
    seen = set()

    def add(code, region):
        key = (code, region) if request.mode == "scan" else code
        if key not in seen:
            seen.add(key)
            rows.append(
                {
                    "code": code,
                    "region": region,
                    "currency": discovery.COUNTRIES[region]["currency"],
                }
            )

    bases = (discovery.KNOWN_BASES if settings.use_known_bases else []) + settings.custom_bases
    for region in regions:
        if request.mode == "scan":
            codes = (
                scan.US_CODES if region == "US" else scan.REGIONS.get(region, {}).get("codes", [])
            )
            for code, _, _ in codes:
                add(code, region)
        else:
            if settings.include_company_lists and request.mode == "discover":
                for code in discovery.build_candidates(region):
                    add(code, region)
            for base in bases:
                add(f"{base}{region.lower()}", region)
                if region == "GB":
                    add(f"{base}uk", region)
                if request.mode == "matrix":
                    add(base, region)
        for code in settings.custom_codes:
            add(code, region)
    if len(rows) > 5000:
        raise ValueError("候选码超过单次任务 5000 条上限，请缩小地区范围或候选列表")
    return rows


class Runner:
    def __init__(self, request, settings, control_file):
        self.request = request
        self.settings = settings
        self.control_file = Path(control_file)
        self.paused = False
        self.done = 0
        self.total = 0

    def checkpoint(self):
        while True:
            try:
                action = json.loads(self.control_file.read_text())["action"]
            except (OSError, ValueError, KeyError):
                action = "run"
            if action == "stop":
                raise Cancelled()
            if action != "pause":
                if self.paused:
                    emit("state", status="running")
                    self.paused = False
                return
            if not self.paused:
                emit("state", status="paused")
                self.paused = True
            time.sleep(0.1)

    def wait(self, duration):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            self.checkpoint()
            time.sleep(min(0.1, max(0, end - time.monotonic())))

    def result(self, row, status, **details):
        self.done += 1
        emit("result", result={**row, "status": status, **details})
        emit("progress", done=self.done, total=self.total, current=row["code"])

    def validate_token(self):
        import config
        from promo_dashboard.network import api_session

        headers = {"Authorization": f"Bearer {config.get_token()}", "Accept": "application/json"}
        self.checkpoint()
        with api_session(self.settings.proxy_url) as session:
            response = session.get(
                "https://chatgpt.com/backend-api/promotions/eligibility/"
                "_dashboard_token_check_000?type=promo",
                headers=headers,
                timeout=self.settings.request_timeout,
            )
        self.guard_status(response.status_code)
        data = response.json()
        if (
            not isinstance(data, dict)
            or "error" in data
            or not (
                isinstance(data.get("is_eligible"), bool)
                or isinstance(data.get("ineligible_reason"), dict)
            )
        ):
            raise RuntimeError("Token 验证没有返回有效数据，请更新连接设置并检查代理")
        emit("log", message="Token 验证通过，开始执行任务")

    @staticmethod
    def guard_status(status):
        if status in (401, 403):
            raise RuntimeError(f"认证或访问被拒绝（HTTP {status}），请检查 Token 和代理后重试")
        if status == 429:
            raise RuntimeError("服务返回 HTTP 429，请稍后重试并增大请求间隔")
        if not 200 <= status < 300:
            raise RuntimeError(f"服务返回 HTTP {status}，请检查网络连接")

    def discovery(self, rows):
        import config
        from promo_dashboard.network import api_session

        with api_session(self.settings.proxy_url) as session:
            headers = {
                "Authorization": f"Bearer {config.get_token()}",
                "Accept": "application/json",
            }
            consecutive_errors = 0
            for row in rows:
                self.checkpoint()
                try:
                    response = session.get(
                        "https://chatgpt.com/backend-api/promotions/eligibility/"
                        f"{row['code']}?type=promo",
                        headers=headers,
                        timeout=self.settings.request_timeout,
                    )
                    self.guard_status(response.status_code)
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("响应不是 JSON 对象")
                    reason = data.get("ineligible_reason") or {}
                    reason_code = reason.get("code", "") if isinstance(reason, dict) else ""
                    status = (
                        "ELIGIBLE"
                        if data.get("is_eligible")
                        else {"user_not_eligible": "EXISTS", "invalid_code": "NOT_FOUND"}.get(
                            reason_code, "ERROR"
                        )
                    )
                    self.result(row, status, detail=reason_code, http_status=response.status_code)
                    consecutive_errors = consecutive_errors + 1 if status == "ERROR" else 0
                except RuntimeError:
                    raise
                except Exception as error:
                    consecutive_errors += 1
                    self.result(row, "ERROR", detail=str(error)[:300])
                if consecutive_errors >= 3:
                    raise RuntimeError("连续 3 次请求异常，已停止任务；请检查日志和连接设置")
                self.wait(self.settings.request_delay)

    def scan(self, rows):
        import auto_scan as scanner
        import discover_codes as discovery

        original_node = None
        nodes = []
        if self.settings.auto_switch:
            self.checkpoint()
            original_node, nodes = scanner.list_nodes()
            emit("log", message=f"Clash 当前节点：{original_node}；可用节点 {len(nodes)} 个")
        try:
            consecutive_errors = 0
            for region in dict.fromkeys(row["region"] for row in rows):
                self.checkpoint()
                region_rows = [row for row in rows if row["region"] == region]
                if self.settings.auto_switch:
                    keywords = self.settings.region_keywords.get(region) or (
                        ["美国", "🇺🇸", "US"]
                        if region == "US"
                        else scanner.REGIONS.get(region, {}).get("keywords")
                        or [region, discovery.COUNTRIES[region]["name"]]
                    )
                    matched = [node for node in nodes if any(key in node for key in keywords)]
                    best, best_delay = None, float("inf")
                    for node in matched:
                        self.checkpoint()
                        latency = scanner.test_latency(node)
                        if 0 < latency < best_delay:
                            best, best_delay = node, latency
                    if not best:
                        for row in region_rows:
                            self.result(row, "SKIPPED", detail="没有匹配到可用地区节点")
                        emit("log", message=f"{region}：没有匹配到可用节点，已跳过")
                        continue
                    scanner.switch_to(best)
                    emit("log", message=f"{region} → {best}（{best_delay}ms）")
                    self.wait(0.5)
                base_data = {}
                if self.request.collect_price:
                    self.checkpoint()
                    base_data = scanner.get_base_checkout(region, region_rows[0]["currency"])
                for row in region_rows:
                    self.checkpoint()
                    url, http_status, error, body = scanner.try_checkout(
                        row["code"], row["region"], row["currency"]
                    )
                    if http_status in (401, 403, 429) or http_status >= 500:
                        self.guard_status(http_status)
                    price = None
                    if url and self.request.collect_price:
                        self.checkpoint()
                        metadata = scanner.fetch_metadata(row["code"])
                        price = scanner.extract_price_info(metadata, base_data, region)
                    reason = body.get("detail", {}) if isinstance(body, dict) else {}
                    reason_code = reason.get("code", "") if isinstance(reason, dict) else ""
                    status = (
                        "ELIGIBLE"
                        if url
                        else {"user_not_eligible": "EXISTS", "invalid_code": "NOT_FOUND"}.get(
                            reason_code, "ERROR"
                        )
                    )
                    self.result(
                        row,
                        status,
                        stripe_url=url,
                        price_info=price,
                        detail=str(error or "")[:300],
                        http_status=http_status,
                    )
                    consecutive_errors = consecutive_errors + 1 if status == "ERROR" else 0
                    if consecutive_errors >= 3:
                        raise RuntimeError("连续 3 次请求异常，已停止任务；请检查日志和连接设置")
                    self.wait(self.settings.request_delay)
        finally:
            if original_node:
                try:
                    scanner.switch_to(original_node)
                    emit("log", message=f"已恢复原节点：{original_node}")
                except Exception:
                    emit("log", message="未能恢复原节点，请在 Clash 中检查当前选择")

    def run(self):
        rows = build_plan(self.request, self.settings)
        self.total = len(rows)
        emit("progress", done=0, total=self.total, current="")
        emit("state", status="running")
        emit(
            "log",
            message=f"已生成 {self.total} 个候选；"
            f"{'预览模式不会调用外部 API' if self.request.preview else '使用本次任务的配置快照'}",
        )
        if self.request.preview:
            for row in rows:
                self.checkpoint()
                self.result(row, "PREVIEW")
        else:
            self.validate_token()
            if self.request.mode == "scan":
                self.scan(rows)
            else:
                self.discovery(rows)
        emit("log", message=f"任务完成，已处理 {self.done}/{self.total} 个候选")


def main():
    payload = json.load(sys.stdin)
    request = JobRequest.model_validate(payload["request"])
    settings = Settings.model_validate(payload["settings"])
    import config

    config.apply_runtime_settings(settings.model_dump())
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(Cancelled()))
    writer = LogWriter()
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            Runner(request, settings, payload["control_file"]).run()
    except Cancelled:
        emit("state", status="cancelled")
        return 130
    except Exception as error:
        emit("error", message=str(error))
        return 1
    finally:
        writer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

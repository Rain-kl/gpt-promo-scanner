"""FastAPI application for the local, single-user dashboard."""

import argparse
import csv
import io
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from promo_dashboard.manager import JobManager
from promo_dashboard.models import ControlRequest, JobRequest, Settings, SettingsUpdate
from promo_dashboard.network import controller_request
from promo_dashboard.storage import ACTIVE_STATES, ROOT, Store

STATIC = Path(__file__).parent / "static"


def public_settings(settings):
    data = settings.model_dump(exclude={"token", "clash_secret"})
    return {
        **data,
        "token_configured": bool(settings.token),
        "clash_secret_configured": bool(settings.clash_secret),
    }


def catalog():
    data = json.loads((ROOT / "known_codes.json").read_text(encoding="utf-8"))
    rows = [
        {**item, "region": region, "status": "REFERENCE"}
        for region, entries in data.get("valid", {}).items()
        for item in entries
    ]
    rows.extend({**item, "status": "EXPIRED"} for item in data.get("expired", []))
    return {"updated_at": data.get("last_updated"), "note": data.get("note"), "results": rows}


def create_app(data_directory=None):
    directory = Path(data_directory or os.getenv("PROMO_PANEL_DATA_DIR", ROOT / ".dashboard"))
    store = Store(directory)
    manager = JobManager(store)

    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.shutdown()

    app = FastAPI(title="Promo Scanner Panel", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default response includes input values, which may contain credentials.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                    for item in error.errors()
                ]
            },
        )

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "此面板只允许本机访问"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            expected = str(request.base_url).rstrip("/")
            if request.headers.get("x-promo-panel") != "1" or (origin and origin != expected):
                return JSONResponse({"detail": "请求来源不匹配，请从本机面板操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_job(job_id):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/bootstrap")
    def bootstrap():
        import auto_scan
        import discover_codes

        return {
            "settings": public_settings(store.settings()),
            "jobs": store.jobs(),
            "regions": [
                {
                    "code": code,
                    **info,
                    "scan_supported": (code == "US" or code in auto_scan.REGIONS),
                }
                for code, info in sorted(discover_codes.COUNTRIES.items())
            ],
            "catalog": catalog(),
        }

    @app.get("/api/settings")
    def get_settings():
        return public_settings(store.settings())

    @app.put("/api/settings")
    def save_settings(update: SettingsUpdate):
        import discover_codes

        if update.default_region not in discover_codes.COUNTRIES and update.default_region != "ALL":
            raise HTTPException(422, "默认地区不受支持")
        if any(region not in discover_codes.COUNTRIES for region in update.region_keywords):
            raise HTTPException(422, "节点关键字包含不受支持的地区")
        old = store.settings()
        data = update.model_dump(exclude={"clear_token", "clear_clash_secret"})
        for field in ("token", "clash_secret"):
            if getattr(update, f"clear_{field}"):
                data[field] = ""
            elif not data[field]:
                data[field] = getattr(old, field)
        settings = Settings.model_validate(data)
        store.save_settings(settings)
        return public_settings(settings)

    @app.post("/api/connections/check")
    def check_connections():
        settings = store.settings()
        results = {
            "token": {
                "ok": bool(settings.token),
                "message": "已配置；将在执行前验证有效性" if settings.token else "尚未配置",
            }
        }
        try:
            data = json.loads(controller_request(settings.model_dump(), "/configs"))
            if not isinstance(data, dict) or "mode" not in data:
                raise ValueError("控制器响应无效")
            results["clash"] = {"ok": True, "message": f"已连接 · {data['mode']} 模式"}
        except httpx.HTTPStatusError as error:
            results["clash"] = {
                "ok": False,
                "message": f"控制器返回 HTTP {error.response.status_code}，请检查地址和密钥",
            }
        except Exception:
            results["clash"] = {
                "ok": False,
                "message": "无法连接控制器，请检查地址或 Socket 和运行状态",
            }
        return results

    @app.get("/api/jobs")
    def get_jobs():
        return {"jobs": store.jobs()}

    @app.post("/api/jobs", status_code=201)
    def start_job(request: JobRequest):
        import discover_codes

        if request.region != "ALL" and request.region not in discover_codes.COUNTRIES:
            raise HTTPException(422, "地区不受支持")
        try:
            return manager.start(request, store.settings())
        except ValueError as error:
            raise HTTPException(409 if "已有任务" in str(error) else 400, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(500, str(error)) from error

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return require_job(job_id)

    @app.post("/api/jobs/{job_id}/control")
    def control_job(job_id: str, request: ControlRequest):
        require_job(job_id)
        try:
            return manager.control(job_id, request.action)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        with manager.lock:
            job = require_job(job_id)
            if job["status"] in ACTIVE_STATES:
                raise HTTPException(409, "请先停止任务，再删除记录")
            store.delete_job(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/logs")
    def get_logs(job_id: str, after: int = Query(0, ge=0)):
        require_job(job_id)
        return {"logs": store.logs(job_id, after)}

    def filtered_results(job_id, q, region, status):
        require_job(job_id)
        rows = store.results(job_id)
        return [
            row
            for row in rows
            if (not q or q.lower() in row["code"].lower())
            and (not region or row["region"] == region)
            and (not status or row["status"] == status)
        ]

    @app.get("/api/jobs/{job_id}/results")
    def get_results(
        job_id: str,
        q: str = "",
        region: str = "",
        status: str = "",
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=100),
    ):
        rows = filtered_results(job_id, q, region, status)
        offset = (page - 1) * per_page
        return {"results": rows[offset : offset + per_page], "total": len(rows), "page": page}

    @app.get("/api/jobs/{job_id}/export")
    def export_results(
        job_id: str, format: str = "json", q: str = "", region: str = "", status: str = ""
    ):
        job = require_job(job_id)
        rows = filtered_results(job_id, q, region, status)
        if format == "json":
            body = json.dumps({"job": job, "results": rows}, ensure_ascii=False, indent=2)
            media_type = "application/json"
        elif format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["code", "region", "status", "estimated_usd", "stripe_url", "detail"])
            for row in rows:
                fields = [
                    row["code"],
                    row["region"],
                    row["status"],
                    (row.get("price_info") or {}).get("actual_usd", ""),
                    row.get("stripe_url") or "",
                    row.get("detail") or "",
                ]
                writer.writerow(
                    ["'" + str(x) if str(x).startswith(("=", "+", "-", "@")) else x for x in fields]
                )
            body = "\ufeff" + output.getvalue()
            media_type = "text/csv"
        else:
            raise HTTPException(400, "仅支持 JSON 和 CSV 导出")
        return Response(
            body,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="promo-{job_id[:8]}.{format}"'},
        )

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="启动本机 Promo Scanner 面板")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)

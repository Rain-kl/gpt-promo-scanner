import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from promo_dashboard.app import create_app
from promo_dashboard.manager import JobManager
from promo_dashboard.models import JobRequest, Settings
from promo_dashboard.network import controller_request
from promo_dashboard.storage import Store
from promo_dashboard.worker import Cancelled, Runner, build_plan


@pytest.fixture
def client(tmp_path):
    application = create_app(tmp_path / "data")
    application.state.store.save_settings(Settings())
    with TestClient(application, headers={"X-Promo-Panel": "1"}) as connection:
        yield connection


def wait_job(client, job_id, statuses, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.025)
    pytest.fail(f"Job did not reach {statuses}: {job}")


def test_settings_round_trip_masks_credentials_and_preserves_blanks(client):
    settings = Settings(
        token="private-login-value",
        clash_secret="private-clash-value",
        proxy_url="",
        request_delay=1.25,
        clash_transport="http",
        custom_codes=["MyCompanyGB"],
        custom_bases=["acme"],
        region_keywords={"GB": ["London", "英国"]},
    ).model_dump()
    response = client.put("/api/settings", json=settings)
    assert response.status_code == 200
    assert "private-login-value" not in response.text
    assert "private-clash-value" not in response.text
    public = response.json()
    assert public["token_configured"] and public["clash_secret_configured"]
    assert public["custom_codes"] == ["mycompanygb"]
    assert public["proxy_url"] == ""
    settings["token"] = settings["clash_secret"] = ""
    response = client.put("/api/settings", json=settings)
    assert response.json()["token_configured"]
    assert client.app.state.store.settings().token == "private-login-value"
    response = client.put(
        "/api/settings", json={**settings, "clear_token": True, "clear_clash_secret": True}
    )
    assert not response.json()["token_configured"]
    assert not response.json()["clash_secret_configured"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_delay", 0),
        ("request_timeout", 1),
        ("clash_url", "ftp://localhost"),
        ("proxy_url", "http://user:password@localhost:7890"),
        ("custom_codes", ["../../config"]),
        ("default_region", "ZZ"),
        ("region_keywords", {"ZZ": ["unknown"]}),
    ],
)
def test_invalid_settings_are_rejected(client, field, value):
    response = client.put("/api/settings", json={**Settings().model_dump(), field: value})
    assert response.status_code == 422


def test_bootstrap_static_and_cross_site_protection(client):
    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    assert len(bootstrap.json()["regions"]) == 34
    assert bootstrap.json()["catalog"]["updated_at"] == "2026-05-12"
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
    data = {"mode": "discover", "preview": True}
    assert (
        client.post(
            "/api/jobs", json=data, headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/settings", headers={"Host": "untrusted.example"}).status_code == 403
    without_header = TestClient(client.app)
    assert without_header.post("/api/jobs", json=data).status_code == 403


def test_real_preview_process_results_filtering_export_and_persistence(client):
    response = client.post("/api/jobs", json={"mode": "discover", "region": "GB", "preview": True})
    assert response.status_code == 201
    job_id = response.json()["id"]
    job = wait_job(client, job_id, {"completed", "failed"})
    assert job["status"] == "completed", job
    assert job["done"] == job["total"] == 28
    assert job["counts"]["preview"] == 28
    rows = client.get(f"/api/jobs/{job_id}/results?per_page=10").json()
    assert rows["total"] == 28
    assert len(rows["results"]) == 10
    assert {row["status"] for row in rows["results"]} == {"PREVIEW"}
    filtered = client.get(f"/api/jobs/{job_id}/results?q=wildmango&status=PREVIEW").json()
    assert filtered["total"] == 2
    assert client.get(f"/api/jobs/{job_id}/results?status=ELIGIBLE").json()["total"] == 0
    exported = client.get(f"/api/jobs/{job_id}/export?format=json&q=wildmango").json()
    assert len(exported["results"]) == 2
    assert exported["job"]["status"] == "completed"
    csv = client.get(f"/api/jobs/{job_id}/export?format=csv")
    assert "attachment" in csv.headers["content-disposition"]
    assert "PREVIEW" in csv.text
    assert len(client.get(f"/api/jobs/{job_id}/logs").json()["logs"]) >= 30
    reloaded = Store(client.app.state.store.directory)
    assert reloaded.job(job_id)["done"] == 28
    assert len(reloaded.results(job_id)) == 28
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_real_worker_pause_resume_stop_and_single_task_guard(client):
    settings = Settings(use_known_bases=False, custom_bases=[f"company{i}" for i in range(80)])
    client.put("/api/settings", json=settings.model_dump())
    response = client.post("/api/jobs", json={"mode": "matrix", "region": "ALL", "preview": True})
    job_id = response.json()["id"]
    assert client.post(f"/api/jobs/{job_id}/control", json={"action": "pause"}).status_code == 200
    job = wait_job(client, job_id, {"paused"})
    assert job["done"] < job["total"]
    assert client.post("/api/jobs", json={"mode": "scan", "preview": True}).status_code == 409
    assert client.delete(f"/api/jobs/{job_id}").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/control", json={"action": "resume"}).status_code == 200
    assert client.post(f"/api/jobs/{job_id}/control", json={"action": "stop"}).status_code == 200
    assert wait_job(client, job_id, {"cancelled"})["ended_at"]
    assert client.post(f"/api/jobs/{job_id}/control", json={"action": "resume"}).status_code == 409


def test_live_task_requires_token_but_preview_does_not(client):
    assert client.post("/api/jobs", json={"mode": "scan", "region": "GB"}).status_code == 400
    assert (
        client.post(
            "/api/jobs", json={"mode": "discover", "region": "ZZ", "preview": True}
        ).status_code
        == 422
    )
    client.put("/api/settings", json=Settings(use_known_bases=False).model_dump())
    assert client.post("/api/jobs", json={"mode": "discover", "preview": True}).status_code == 400


def test_runtime_settings_override_injected_environment(monkeypatch):
    import config

    monkeypatch.setattr(config, "_runtime", None)
    monkeypatch.setenv("HTTP_PROXY", "http://inherited.invalid:8080")
    config.apply_runtime_settings(
        Settings(proxy_url="", token="private", rule_group="custom").model_dump()
    )
    assert config.get_proxy_url() == ""
    assert config.get_token() == "private"
    assert config.get_proxy_group("rule") == "custom"
    monkeypatch.setattr(config, "_runtime", None)


def test_matrix_and_custom_candidates_are_deduplicated_and_bounded():
    settings = Settings(use_known_bases=False, custom_bases=["acme"], custom_codes=["acmegb"])
    plan = build_plan(JobRequest(mode="matrix", region="GB"), settings)
    assert {row["code"] for row in plan} == {"acme", "acmegb", "acmeuk"}
    with pytest.raises(ValueError, match="5000"):
        build_plan(
            JobRequest(mode="matrix", region="ALL"),
            Settings(custom_bases=[f"company{i}" for i in range(160)]),
        )


def test_restart_marks_active_jobs_interrupted(tmp_path):
    store = Store(tmp_path)
    store.save_job({"id": "old", "status": "running"})
    JobManager(store)
    assert store.job("old")["status"] == "interrupted"


def test_redaction_covers_nested_results_and_logs():
    settings = Settings(token="private-token", clash_secret="private-key")
    redacted = JobManager.redact({"detail": ["private-token / private-key"]}, settings)
    assert "private-token" not in json.dumps(redacted)
    assert "private-key" not in json.dumps(redacted)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_network_failures_stop_without_misclassifying_codes(status):
    with pytest.raises(RuntimeError):
        Runner.guard_status(status)


def test_worker_checkpoint_stop_is_cooperative(tmp_path):
    control = tmp_path / "control.json"
    control.write_text('{"action":"stop"}')
    runner = Runner(JobRequest(mode="discover"), Settings(), control)
    with pytest.raises(Cancelled):
        runner.checkpoint()


def test_controller_supports_http_secret_and_bypasses_inherited_proxy(monkeypatch):
    captured = {}

    def handle(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"mode": "rule"})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handle))
    data = controller_request(
        Settings(clash_transport="http", clash_secret="private-key").model_dump(), "/configs"
    )
    assert json.loads(data)["mode"] == "rule"
    assert captured["url"] == "http://127.0.0.1:9090/configs"
    assert captured["auth"] == "Bearer private-key"


def test_sqlite_credentials_file_is_private(client):
    if Path("/proc").exists():
        assert client.app.state.store.path.stat().st_mode & 0o777 == 0o600


def test_invalid_input_does_not_echo_credentials(client):
    response = client.put("/api/settings", json={"token": {"secret": "never-echo-this"}})
    assert response.status_code == 422
    assert "never-echo-this" not in response.text


def test_older_history_is_not_silently_hidden(tmp_path):
    store = Store(tmp_path)
    for index in range(105):
        store.save_job({"id": str(index), "status": "completed"})
    assert len(store.jobs()) == 105
    assert store.jobs()[-1]["id"] == "0"

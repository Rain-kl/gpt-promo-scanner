from pathlib import Path
from types import SimpleNamespace

import pytest

from promo_dashboard.models import JobRequest, Settings
from promo_dashboard.worker import Cancelled, Runner


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        "promo_dashboard.worker.emit", lambda kind, **data: recorded.append({"type": kind, **data})
    )
    monkeypatch.setattr("config.get_token", lambda: "test-token")
    return recorded


def response(status, data):
    return SimpleNamespace(status_code=status, json=lambda: data)


class FakeSession:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.replies)


def make_runner(tmp_path, **options):
    runner = Runner(JobRequest(mode="discover"), Settings(**options), tmp_path / "control")
    runner.wait = lambda _: None
    return runner


def test_discovery_classifies_actual_api_outcomes_and_uses_timeout(tmp_path, monkeypatch, events):
    session = FakeSession(
        [
            response(200, {"is_eligible": True}),
            response(200, {"ineligible_reason": {"code": "user_not_eligible"}}),
            response(200, {"ineligible_reason": {"code": "invalid_code"}}),
            response(200, ["unexpected"]),
        ]
    )
    monkeypatch.setattr("promo_dashboard.network.api_session", lambda _: session)
    runner = make_runner(tmp_path, request_timeout=35)
    runner.total = 4
    runner.discovery([{"code": f"candidate{i}", "region": "GB"} for i in range(4)])
    results = [event["result"] for event in events if event["type"] == "result"]
    assert [row["status"] for row in results] == ["ELIGIBLE", "EXISTS", "NOT_FOUND", "ERROR"]
    assert all(kwargs["timeout"] == 35 for _, kwargs in session.calls)
    assert runner.done == 4 and session.closed


@pytest.mark.parametrize(
    "status,data",
    [
        (401, {"detail": "Unauthorized"}),
        (403, {}),
        (429, {}),
        (200, {"error": "invalid session"}),
        (200, []),
        (200, {}),
    ],
)
def test_invalid_token_responses_never_become_valid(tmp_path, monkeypatch, events, status, data):
    session = FakeSession([response(status, data)])
    monkeypatch.setattr("promo_dashboard.network.api_session", lambda _: session)
    runner = make_runner(tmp_path)
    with pytest.raises(RuntimeError):
        runner.validate_token()
    assert session.closed
    assert not any(event["type"] == "result" for event in events)


def test_valid_token_requires_expected_payload(tmp_path, monkeypatch, events):
    session = FakeSession(
        [response(200, {"is_eligible": False, "ineligible_reason": {"code": "invalid_code"}})]
    )
    monkeypatch.setattr("promo_dashboard.network.api_session", lambda _: session)
    make_runner(tmp_path).validate_token()
    assert session.closed
    assert events[0]["type"] == "log"


def test_repeated_unexpected_responses_stop_discovery(tmp_path, monkeypatch, events):
    session = FakeSession([response(200, {"error": "bad response"})] * 4)
    monkeypatch.setattr("promo_dashboard.network.api_session", lambda _: session)
    runner = make_runner(tmp_path)
    with pytest.raises(RuntimeError, match="连续 3 次"):
        runner.discovery([{"code": f"candidate{i}", "region": "GB"} for i in range(4)])
    assert len(session.calls) == 3
    assert runner.done == 3


def test_scan_retains_checkout_results_and_restores_node(tmp_path, monkeypatch, events):
    import auto_scan

    switched = []
    monkeypatch.setattr(auto_scan, "list_nodes", lambda: ("original", ["英国 London"]))
    monkeypatch.setattr(auto_scan, "test_latency", lambda _: 12)
    monkeypatch.setattr(auto_scan, "switch_to", switched.append)
    replies = iter(
        [
            ("https://checkout.stripe.com/example", 200, None, {}),
            (None, 400, "invalid_code", {"detail": {"code": "invalid_code"}}),
        ]
    )
    monkeypatch.setattr(auto_scan, "try_checkout", lambda *_: next(replies))
    runner = Runner(JobRequest(mode="scan", collect_price=False), Settings(), tmp_path / "control")
    runner.wait = lambda _: None
    runner.total = 2
    runner.scan([{"code": f"candidate{i}", "region": "GB", "currency": "GBP"} for i in range(2)])
    assert switched == ["英国 London", "original"]
    results = [event["result"] for event in events if event["type"] == "result"]
    assert [row["status"] for row in results] == ["ELIGIBLE", "NOT_FOUND"]
    assert results[0]["stripe_url"].startswith("https://checkout.stripe.com/")


def test_scan_cancellation_attempts_to_restore_node(tmp_path, monkeypatch, events):
    import auto_scan

    switched = []
    monkeypatch.setattr(auto_scan, "list_nodes", lambda: ("original", ["英国 London"]))
    monkeypatch.setattr(auto_scan, "test_latency", lambda _: 12)
    monkeypatch.setattr(auto_scan, "switch_to", switched.append)
    runner = Runner(JobRequest(mode="scan", collect_price=False), Settings(), tmp_path / "control")
    runner.wait = lambda _: None
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 4:
            raise Cancelled()

    runner.checkpoint = checkpoint
    with pytest.raises(Cancelled):
        runner.scan([{"code": "candidate", "region": "GB", "currency": "GBP"}])
    assert switched == ["英国 London", "original"]


def test_unmatched_nodes_are_explicitly_skipped(tmp_path, monkeypatch, events):
    monkeypatch.setattr("auto_scan.list_nodes", lambda: ("original", ["US New York"]))
    switched = []
    monkeypatch.setattr("auto_scan.switch_to", switched.append)
    runner = make_runner(tmp_path)
    runner.scan([{"code": "candidate", "region": "GB", "currency": "GBP"}])
    results = [event["result"] for event in events if event["type"] == "result"]
    assert results[0]["status"] == "SKIPPED"
    assert switched == ["original"]


def test_explicit_proxy_and_tls_verification_are_preserved():
    from curl_cffi import CurlOpt

    from promo_dashboard.network import api_session

    with api_session("") as session:
        assert session.proxies == {"http": "", "https": ""}
        assert session.curl_options[CurlOpt.NOPROXY] == ""
        assert session.verify is True or (
            isinstance(session.verify, str) and Path(session.verify).is_file()
        )

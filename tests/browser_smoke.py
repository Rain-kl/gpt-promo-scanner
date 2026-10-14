"""Exercise the real local UI with Chromium, using only offline preview tasks.

Run: uv run python tests/browser_smoke.py
Screenshots are saved to the ignored test-artifacts directory.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "test-artifacts"


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="promo-browser-") as directory:
        environment = {**os.environ, "PROMO_PANEL_DATA_DIR": directory}
        with open(Path(directory) / "server.log", "w+") as server_log:
            process = subprocess.Popen(
                [sys.executable, "-m", "promo_dashboard", "--port", str(port)],
                cwd=ROOT,
                env=environment,
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        with urlopen(base + "/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.1)
                else:
                    server_log.seek(0)
                    raise RuntimeError(server_log.read())
                exercise_browser(base)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def exercise_browser(base):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=shutil.which("chromium") or shutil.which("google-chrome"),
            args=["--no-sandbox"],
            headless=True,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        # Prevent accidental external requests during UI verification.
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base) else route.abort()
            ),
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        expect(page.locator("#page-workspace")).to_be_visible()
        expect(page.locator("#run-status")).to_have_text("等待任务")
        page.screenshot(path=ARTIFACTS / "desktop-empty.png", full_page=True)

        page.locator('[data-route="settings"]').click()
        page.locator("#setting-proxy").fill("")
        page.locator("#setting-transport").select_option("http")
        page.locator("#setting-clash-url").fill("http://127.0.0.1:9")
        page.locator("#setting-codes").fill("manualgb")
        page.locator("#setting-custom-bases").fill("acme")
        page.locator("#setting-delay").fill("1.2")
        page.locator("#save-settings").click()
        expect(page.locator("#settings-saved-state")).to_have_text("已保存")
        settings = page.request.get(base + "/api/settings").json()
        assert settings["proxy_url"] == "" and settings["custom_codes"] == ["manualgb"]
        page.locator("#check-connections").click()
        expect(page.locator("#connection-check-result")).to_contain_text("无法连接控制器")
        page.screenshot(path=ARTIFACTS / "desktop-settings.png", full_page=True)

        page.get_by_role("link", name="扫描工作台", exact=True).click()
        page.locator("#preview-button").click()
        expect(page.locator("#run-status")).to_have_text("已完成", timeout=15000)
        expect(page.locator("#run-progress-text")).to_have_text("31 / 31")
        expect(page.locator("#result-body tr")).to_have_count(31)
        page.screenshot(path=ARTIFACTS / "desktop-results.png", full_page=True)
        page.locator("#result-search").fill("manual")
        expect(page.locator("#result-body tr")).to_have_count(1)
        page.locator("#result-status").select_option("ELIGIBLE")
        expect(page.locator("#results-empty")).to_be_visible()
        page.locator("#result-status").select_option("")
        page.locator("#result-search").fill("")
        expect(page.locator("#result-body tr")).to_have_count(31)
        with page.expect_download() as download:
            page.locator("#export-json").click()
        path = ARTIFACTS / "preview-export.json"
        download.value.save_as(path)
        assert len(json.loads(path.read_text())["results"]) == 31
        page.locator("#tab-results").focus()
        page.keyboard.press("ArrowRight")
        expect(page.locator("#tab-logs")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#log-stream")).to_contain_text("预览模式不会调用外部 API")
        page.locator("#tab-results").click()

        for route in ("workspace", "settings", "catalog", "history"):
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator(f'[data-route="{route}"]').click()
            expect(page.locator(f"#page-{route}")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), route
            page.screenshot(path=ARTIFACTS / f"mobile-{route}.png", full_page=True)

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('[data-route="catalog"]').click()
        page.locator("#catalog-region").select_option("US")
        expect(page.locator("#catalog-body tr")).to_have_count(11)
        page.locator("#catalog-search").fill("nothing-matches-this")
        expect(page.locator("#catalog-empty")).to_be_visible()
        page.locator('[data-route="history"]').click()
        page.get_by_role("button", name="删除", exact=True).click()
        page.wait_for_timeout(1800)  # Confirmation must survive automatic history refresh.
        expect(page.get_by_role("button", name="确认删除", exact=True)).to_be_visible()
        page.get_by_role("button", name="取消", exact=True).click()

        # A larger offline preview gives enough work to exercise real pause/resume/stop controls.
        page.locator('[data-route="settings"]').click()
        page.locator("#setting-bases").uncheck()
        page.locator("#setting-custom-bases").fill("\n".join(f"company{i}" for i in range(80)))
        page.locator("#setting-codes").fill("")
        page.locator("#setting-mode").select_option("matrix")
        page.locator("#setting-region").select_option("ALL")
        page.locator("#save-settings").click()
        expect(page.locator("#settings-saved-state")).to_have_text("已保存")
        page.locator('[data-route="workspace"]').click()
        page.locator("#preview-button").click()
        page.locator("#pause-button").click()
        expect(page.locator("#run-status")).to_have_text("已暂停", timeout=10000)
        page.locator("#resume-button").click()
        page.locator("#stop-button").click()
        expect(page.locator("#run-status")).to_have_text("已停止", timeout=10000)
        assert not errors, errors
        print(
            json.dumps(
                {
                    "browser": "Chromium",
                    "status": "passed",
                    "checks": [
                        "settings",
                        "offline preview",
                        "filters",
                        "JSON export",
                        "keyboard tabs",
                        "task history",
                        "catalog",
                        "pause/resume/stop",
                        "390px responsive routes",
                        "no JavaScript errors",
                    ],
                },
                ensure_ascii=False,
            )
        )
        context.close()
        browser.close()


if __name__ == "__main__":
    main()

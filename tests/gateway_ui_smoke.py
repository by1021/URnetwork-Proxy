"""Opt-in browser regression: real management HTTP API, deterministic upstreams.

Run with an environment containing Playwright and Chromium:
    python3 tests/gateway_ui_smoke.py
No account credentials, remote proxies or existing gateway data are used.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright

from core.config import DEFAULT_CONFIG
from core.gateway import GatewayManager
from core.web_server import ManagementApp, ManagementServer
from test_gateway_country import FixtureClient, FixtureRelay, sample


def main():
    with tempfile.TemporaryDirectory(prefix="ur-gateway-ui-") as tmp:
        config = {**DEFAULT_CONFIG, "web_username": "fixture-user", "web_password": "fixture-password"}
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        client = FixtureClient()
        client.api_base = config["api_base_url"]
        client._lock = threading.RLock()
        client.get_account_status = lambda: {}
        samples = [sample("VN"), sample("US", "203.0.113.2")]

        async def probe(upstream):
            return samples.pop(0) if len(samples) > 1 else dict(samples[0])

        manager = GatewayManager(client, Path(tmp) / "gateways.json", probe=probe, relay_factory=FixtureRelay)
        app = ManagementApp(config, tmp, manager=manager, client=client, config_path=config_path,
                            web_host="127.0.0.1", web_port=0)
        app.catalog.get = lambda refresh=False: {"countries": [
            {"code": "US", "name": "United States", "location_id": "us-country", "providers": 10},
            {"code": "VN", "name": "Vietnam", "location_id": "vn-country", "providers": 5}],
            "source": "/network/provider-locations", "stale": False, "error": None, "updated_at": None}
        app.start()
        server = ManagementServer(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Saved/stopped gateways still reserve their ports. Legacy ports stay editable.
            app.call(manager.create_gateway({"name": "Reserved", "port": 31000, "enabled": False}))
            app.call(manager.create_gateway({"name": "Legacy", "port": 10801, "enabled": False}))
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.locator("#login-username").fill("fixture-user")
                page.locator("#login-password").fill("fixture-password")
                page.locator("#login-form").get_by_role("button", name="登录", exact=True).click()
                expect(page.locator("#login-dialog")).not_to_be_visible()
                expect(page.locator("#gateway-list")).to_contain_text("Reserved")

                page.get_by_role("button", name="新建网关", exact=True).click()
                expect(page.get_by_label("本地端口", exact=True)).to_have_value("31001")
                page.get_by_label("网关名称", exact=True).fill("US verified fixture")
                page.get_by_label("本地端口", exact=True).fill("31000")
                page.get_by_role("button", name="创建并启动", exact=True).click()
                expect(page.locator("#port-error")).to_contain_text("已被其他网关使用")
                assert not client.creates
                page.get_by_label("本地端口", exact=True).fill("31001")
                page.get_by_role("button", name="创建并启动", exact=True).click()
                expect(page.locator("#country-picker-error")).to_contain_text("请选择国家")
                assert not client.creates
                page.get_by_label("选择出口国家或地区", exact=True).select_option("us-country")
                page.get_by_role("button", name="创建并启动", exact=True).click()
                expect(page.locator("#gateway-dialog")).not_to_be_visible()
                card = page.locator(".gateway-card").filter(has=page.get_by_role("heading", name="US verified fixture", exact=True))
                expect(card).to_contain_text("实测国家")
                expect(card).to_contain_text("美国 (US)")
                assert len(client.creates) == 2
                assert all(c["target"] == "US" and c["location_id"] == "us-country" for c in client.creates)
                assert "fixture-1" in client.removed

                # A rejected country edit keeps the old binding and reports failure,
                # rather than pretending the requested new country was saved.
                card.get_by_role("button", name="编辑 US verified fixture", exact=True).click()
                page.get_by_label("选择出口国家或地区", exact=True).select_option("vn-country")
                page.get_by_role("button", name="保存更改", exact=True).click()
                expect(page.locator("#gateway-dialog")).not_to_be_visible()
                expect(page.locator("#toasts")).to_contain_text("出口国家与所选国家不一致")
                expect(page.locator("#toasts")).not_to_contain_text("网关设置已保存")
                saved = app.call(manager.list_gateways())
                assert next(g for g in saved if g["name"] == "US verified fixture")["country_code"] == "US"

                # Advance only the fixture retry deadline, not the maintenance clock.
                async def allow_retry():
                    for runtime in manager._gateways.values():
                        runtime.next_create = 0
                app.call(allow_retry())

                legacy = page.locator(".gateway-card").filter(has=page.get_by_role("heading", name="Legacy", exact=True))
                legacy.get_by_role("button", name="编辑 Legacy", exact=True).click()
                expect(page.get_by_label("本地端口", exact=True)).to_have_value("10801")
                page.locator("#gateway-cancel").click()

                # Exhausting only wrong-country candidates must show the explicit error.
                samples[:] = [sample("VN")]
                card.get_by_role("button", name="换线 US verified fixture", exact=True).click()
                expect(page.locator("#toasts")).to_contain_text("出口国家与所选国家不一致")
                expect(page.locator("#toasts")).not_to_contain_text("换线完成")
                expect(card).to_contain_text("203.0.113.2")
                page.get_by_role("button", name="新建网关", exact=True).click()
                expect(page.get_by_label("本地端口", exact=True)).to_have_value("31002")
                page.get_by_label("网关名称", exact=True).fill("US rejected fixture")
                page.get_by_label("选择出口国家或地区", exact=True).select_option("us-country")
                page.get_by_role("button", name="创建并启动", exact=True).click()
                expect(page.locator("#gateway-dialog")).not_to_be_visible()
                rejected = page.locator(".gateway-card").filter(has=page.get_by_role("heading", name="US rejected fixture", exact=True))
                expect(rejected).to_contain_text("出口国家与所选国家不一致")
                expect(rejected).not_to_contain_text("运行中")
                assert not errors, errors
                browser.close()
            print("PASS: default port, legacy edit, form validation, US selection, VN rejection, measured country UI")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == "__main__":
    main()

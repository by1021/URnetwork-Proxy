"""Optional real Chromium UI regression; local fixtures only, no cloud resources."""
import json
import tempfile
import threading
from pathlib import Path

from core.gateway import GatewayManager
from core.web_server import ManagementApp, ManagementServer
from tests.test_integration import free_port


class BrowserClient:
    api_base = "https://api.bringyour.com"
    def __init__(self):
        self.serial = 0
        self.offline = False

    def _request(self, *args, **kwargs):
        if self.offline:
            raise OSError("Fixture offline")
        countries = [("US", "United States", 20000), ("JP", "Japan", 210),
                     ("DE", "Germany", 780), ("GB", "United Kingdom", 450)]
        countries += [("A" + chr(65+i), "Test " + chr(65+i), 30-i) for i in range(26)]
        return {"locations": [dict(location_id=code, location_type="country",
            country_code=code.lower(), name=name, provider_count=count, stable=True)
            for code, name, count in countries]}

    def get_account_status(self):
        return dict(network_name="本地验证工作空间", daily_quota_gib=10, remaining_gib=8.4,
                    used_gib=1.6, plan_tier="standard", auth_method="api_key")

    def create_proxy(self, **kwargs):
        self.serial += 1
        return dict(client_id=f"fixture-{self.serial}", host="127.0.0.1", http_port=1,
                    socks_port=1, auth_token="fixture-only", password="none")

    def remove_proxy(self, client_id):
        return {}


async def probe(upstream):
    return dict(ok=True, http_ok=True, socks_ok=True, exit_ip="203.0.113.10", latency_ms=38, error="")


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory() as directory:
        client = BrowserClient()
        manager = GatewayManager(client, Path(directory)/"gateways.json", probe=probe)
        app = ManagementApp({}, directory, manager=manager, client=client,
                            config_path=Path(directory) / "config.json")
        server = ManagementServer(("127.0.0.1", 0), app)
        app.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and "Content Security Policy" in msg.text else None)
                page.goto(f"http://127.0.0.1:{server.server_port}/#token={app.token}")
                expect(page.locator("#login-dialog")).not_to_be_visible(timeout=10000)
                expect(page.locator("#stat-countries")).to_have_text("30", timeout=10000)
                assert "#token" not in page.url
                for name, code in (("美国 · 工作", "US"), ("日本 · 浏览", "JP")):
                    page.locator("#add-gateway").click()
                    page.locator("#gateway-name").fill(name)
                    page.locator("#gateway-port").fill(str(free_port()))
                    page.locator("#gateway-mode").select_option("country")
                    page.locator("#gateway-country-search").fill(code)
                    page.locator("#gateway-country").select_option(value=code)
                    page.locator("#gateway-save").click()
                    try:
                        expect(page.locator("#gateway-dialog")).not_to_be_visible(timeout=10000)
                    except AssertionError:
                        print("UI ERROR:", page.locator("#gateway-error").text_content(), "JS:", errors)
                        print("GATEWAYS:", app.call(manager.list_gateways()))
                        print("EVENTS:", manager.events())
                        raise
                expect(page.locator("#stat-total")).to_have_text("2", timeout=10000)
                expect(page.locator("#stat-running")).to_have_text("2", timeout=10000)
                page.get_by_role("button", name="国家与地区", exact=True).click()
                page.locator("#country-search").fill("日本")
                expect(page.locator("#country-list")).to_contain_text("JP")
                page.locator("#country-search").fill("")
                client.offline = True
                page.locator("#refresh-countries").click()
                expect(page.locator("#view-countries [data-country-stale]")).to_be_visible(timeout=10000)
                client.offline = False
                page.locator("#refresh-countries").click()
                expect(page.locator("#view-countries [data-country-stale]")).to_contain_text("未过期")
                page.get_by_role("button", name="网关总览", exact=False).first.click()
                screenshot = Path(__file__).resolve().parents[1]/"docs"/"assets"/"dashboard.png"
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                expect(page.locator("#toasts .toast")).to_have_count(0, timeout=7000)
                page.screenshot(path=str(screenshot), full_page=True)
                page.locator("#theme-toggle").click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(screenshot.with_name("dashboard-mobile.png")), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Mobile horizontal overflow"
                page.set_viewport_size({"width": 1440, "height": 1100})
                # Exercise actual authenticated management actions from the cards.
                us = page.locator(".gateway-card").filter(has_text="美国 · 工作")
                us.get_by_role("button", name="停止 美国 · 工作", exact=True).click()
                expect(us).to_contain_text("已停止", timeout=10000)
                us.get_by_role("button", name="启动 美国 · 工作", exact=True).click()
                expect(us.get_by_role("button", name="停止 美国 · 工作", exact=True)).to_be_enabled(timeout=10000)
                us.get_by_role("button", name="检查 美国 · 工作", exact=True).click()
                expect(us.get_by_role("button", name="检查 美国 · 工作", exact=True)).to_be_enabled(timeout=10000)
                us.get_by_role("button", name="编辑 美国 · 工作", exact=True).click()
                page.locator("#gateway-name").fill("美国 · 已编辑")
                page.locator("#gateway-save").click()
                expect(page.locator("#gateway-dialog")).not_to_be_visible(timeout=10000)
                updated = page.locator(".gateway-card").filter(has_text="美国 · 已编辑")
                updated.get_by_role("button", name="换线 美国 · 已编辑", exact=True).click()
                expect(updated).to_contain_text("换线 1 次", timeout=10000)
                updated.get_by_role("button", name="删除 美国 · 已编辑", exact=True).click()
                page.locator("#confirm-delete").click()
                expect(page.locator("#delete-dialog")).not_to_be_visible(timeout=10000)
                expect(page.locator("#stat-total")).to_have_text("1", timeout=10000)
                page.locator("#logout").click()
                expect(page.locator("#login-dialog")).to_be_visible()
                assert not errors, errors
                browser.close()
                print("Browser PASS: login, 30 countries, two country-bound gateways, stale cache, responsive/theme, edit/start/stop/check/rotate/delete, logout; no JS/CSP errors")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.close()


if __name__ == "__main__":
    main()

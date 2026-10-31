"""Real browser + real config API, temporary files and offline upstream fixtures."""
import json
from pathlib import Path
import tempfile
import threading

from core.config import DEFAULT_CONFIG, KEEP_SECRET, parse_config
from core.gateway import GatewayManager
from core.web_server import ManagementApp, ManagementServer
from tests.browser_smoke import BrowserClient, probe


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        initial = {**DEFAULT_CONFIG, "api_key": "fixture-private-existing", "custom": {"pattern": ",}"}}
        path.write_text(json.dumps(initial), encoding="utf-8")
        client = BrowserClient()
        manager = GatewayManager(client, Path(directory) / "gateways.json", probe=probe)
        app = ManagementApp(initial, directory, manager=manager, client=client, config_path=path)
        server = ManagementServer(("127.0.0.1", 0), app)
        app.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.on("console", lambda msg: errors.append(msg.text)
                        if msg.type == "error" and "Content Security Policy" in msg.text else None)
                page.goto(f"http://127.0.0.1:{server.server_port}/#token={app.token}")
                expect(page.locator("#login-dialog")).not_to_be_visible()
                page.get_by_role("button", name="配置面板", exact=True).click()
                expect(page.locator("#settings-save")).to_be_enabled()
                expect(page.locator("#config-api_key")).to_have_value(KEEP_SECRET)
                expect(page.locator("#web-server-port")).to_have_text(str(server.server_port))
                assert "fixture-private-existing" not in page.content()

                page.locator("#config-timeout_seconds").fill("25")
                page.locator("#config-web_port").fill("8899")
                page.locator("#config-default_country").fill("JP")
                page.locator("#config-default_mode").select_option("country")
                expect(page.locator("#config-dirty")).to_have_text("有未保存修改")
                page.get_by_role("tab", name="源码编辑").click()
                expect(page.locator("#config-code")).to_be_visible()
                source = page.locator("#config-source")
                value = json.loads(source.input_value())
                assert value["timeout_seconds"] == 25
                value["timeout_seconds"] = 32
                value["extension"] = {"path": "https://example.invalid/,}/*string*/"}
                source.fill("// edited with JSONC\n" + json.dumps(value, ensure_ascii=False)[:-1] + ",}")
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#config-visual")).to_be_visible()
                expect(page.locator("#config-timeout_seconds")).to_have_value("32")
                page.locator("#config-default_country").fill("DE")
                page.locator("#settings-save").click()
                expect(page.locator("#settings-success")).to_contain_text("已保存到 config.json")
                saved = parse_config(path.read_text(encoding="utf-8"))
                assert saved["timeout_seconds"] == 32 and saved["default_country"] == "DE"
                assert saved["api_key"] == "fixture-private-existing"
                assert saved["extension"] == value["extension"] and saved["custom"] == initial["custom"]
                expect(page.locator("#config-restart")).to_be_visible()

                page.get_by_role("tab", name="源码编辑").click()
                expect(page.locator("#config-code")).to_be_visible()
                before = path.read_bytes()
                bad = json.loads(source.input_value())
                bad["web_port"] = 65536
                source.fill(json.dumps(bad))
                page.locator("#settings-save").click()
                expect(page.locator("#settings-error")).to_contain_text("web_port")
                assert path.read_bytes() == before
                source.fill('{"api_key": "private-new", "api_key": "duplicate"}')
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#settings-error")).to_contain_text("重复")
                expect(source).to_be_visible()
                assert "private-new" not in page.locator("#settings-error").inner_text()
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#config-discard").click()
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#config-visual")).to_be_visible()
                expect(page.locator("#config-timeout_seconds")).to_be_visible()

                # Compare-and-save refuses to overwrite an external file edit.
                page.locator("#config-timeout_seconds").fill("44")
                outside = parse_config(path.read_text(encoding="utf-8"))
                outside["web_port"] = 9011
                path.write_text(json.dumps(outside), encoding="utf-8")
                page.locator("#settings-save").click()
                expect(page.locator("#settings-error")).to_contain_text("修改")
                expect(page.locator("#config-timeout_seconds")).to_have_value("44")
                assert parse_config(path.read_text(encoding="utf-8"))["timeout_seconds"] == 32
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.locator("#refresh-settings").click()
                expect(page.locator("#config-timeout_seconds")).to_have_value("44")
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#refresh-settings").click()
                expect(page.locator("#config-web_port")).to_have_value("9011")
                expect(page.locator("#config-timeout_seconds")).to_have_value("32")

                # Cancel navigation preserves the draft; confirm discards it.
                page.locator("#config-timeout_seconds").fill("40")
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.get_by_role("button", name="国家与地区", exact=True).click()
                expect(page.locator("#view-settings")).to_be_visible()
                expect(page.locator("#config-timeout_seconds")).to_have_value("40")
                page.once("dialog", lambda dialog: dialog.accept())
                page.get_by_role("button", name="国家与地区", exact=True).click()
                page.get_by_role("button", name="配置面板", exact=True).click()
                expect(page.locator("#config-timeout_seconds")).to_have_value("32")

                # Format is a draft operation, new credentials survive mode changes,
                # and after save the response contains only placeholders again.
                page.get_by_role("tab", name="源码编辑").click()
                expect(page.locator("#config-code")).to_be_visible()
                value = json.loads(source.input_value())
                value["api_key"] = "fixture-private-replacement"
                source.fill("// new credential\n" + json.dumps(value))
                page.locator("#config-format").click()
                expect(page.locator("#settings-success")).to_contain_text("尚未写入")
                assert parse_config(path.read_text(encoding="utf-8"))["api_key"] == "fixture-private-existing"
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#config-visual")).to_be_visible()
                expect(page.locator("#config-api_key")).to_have_value("fixture-private-replacement")
                page.locator("#settings-save").click()
                expect(page.locator("#settings-success")).to_contain_text("已保存到")
                expect(page.locator("#config-api_key")).to_have_value(KEEP_SECRET)
                assert parse_config(path.read_text(encoding="utf-8"))["api_key"] == "fixture-private-replacement"
                assert "fixture-private-replacement" not in source.input_value()

                # Extension numbers stay in authoritative source text, never JS Number.
                page.get_by_role("tab", name="源码编辑").click()
                expect(page.locator("#config-code")).to_be_visible()
                value = json.loads(source.input_value())
                value["extension"]["serial"] = 9007199254740993
                source.fill(json.dumps(value))
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#config-visual")).to_be_visible()
                expect(page.locator("#config-timeout_seconds")).to_be_visible()
                page.locator("#config-timeout_seconds").fill("35")
                page.get_by_role("tab", name="源码编辑").click()
                expect(page.locator("#config-code")).to_be_visible()
                expect(source).to_be_visible()
                assert json.loads(source.input_value())["extension"]["serial"] == 9007199254740993
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#config-visual")).to_be_visible()
                page.locator("#settings-save").click()
                expect(page.locator("#settings-success")).to_contain_text("已保存到")
                assert parse_config(path.read_text(encoding="utf-8"))["extension"]["serial"] == 9007199254740993

                screenshots = Path(__file__).resolve().parents[1] / "docs" / "assets"
                screenshots.mkdir(parents=True, exist_ok=True)
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(screenshots / "config-panel.png"), full_page=True)
                page.get_by_role("tab", name="源码编辑").click()
                expect(page.locator("#config-code")).to_be_visible()
                page.screenshot(path=str(screenshots / "config-panel-source.png"), full_page=True)
                page.locator("#theme-toggle").click()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Source mobile overflow"
                page.get_by_role("tab", name="可视化编辑").click()
                expect(page.locator("#config-visual")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Form mobile overflow"
                page.screenshot(path=str(screenshots / "config-panel-mobile.png"), full_page=True)
                page.locator("#logout").click()
                expect(page.locator("#login-dialog")).to_be_visible()
                expect(source).to_have_value("")
                expect(page.locator("#config-api_key")).to_have_value("")
                assert not errors, errors
                browser.close()
                print("Config browser PASS: form/source sync, JSONC, disk persistence, secret preservation/replacement, validation/conflict/no-write, dirty navigation, desktop/mobile light/dark, logout; no JS/CSP errors")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.close()


if __name__ == "__main__":
    main()

"""Opt-in UI regressions using temporary management HTTP services and real clipboard.

Run: python3 tests/login_clipboard_ui_smoke.py
Requires Playwright and Chromium only in the test environment. No live account,
gateway data or remote upstream is used. Artifacts may be saved with --artifacts.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright

from core.config import DEFAULT_CONFIG
from core.gateway import GatewayManager
from core.web_server import ManagementApp, ManagementServer
from test_gateway_country import FixtureClient, FixtureRelay, sample

ARTIFACTS = None


class LoginClipboardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = []
        cls.addClassCleanup(lambda: [cleanup() for cleanup in reversed(cls.resources)])
        tmp = tempfile.TemporaryDirectory(prefix="ur-login-clipboard-")
        cls.resources.append(tmp.cleanup)
        config = {**DEFAULT_CONFIG, "web_username": "fixture-user", "web_password": "fixture-password",
                  "allowed_hosts": ["127.0.0.1", "[::1]", "proxy-ui.test"]}
        config_path = Path(tmp.name) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        client = FixtureClient()
        client.api_base = config["api_base_url"]
        client._lock = threading.RLock()
        client.get_account_status = lambda: {}

        async def probe(upstream):
            return sample("US")

        manager = GatewayManager(client, Path(tmp.name) / "gateways.json", probe=probe, relay_factory=FixtureRelay)
        cls.app = ManagementApp(config, tmp.name, manager=manager, client=client, config_path=config_path,
                                web_host="127.0.0.1", web_port=0)
        cls.resources.append(cls.app.close)
        cls.app.catalog.get = lambda refresh=False: {"countries": [], "source": "fixture", "stale": False,
                                                     "error": None, "updated_at": None}
        cls.app.start()
        cls.app.call(manager.create_gateway({"name": "Copy fixture", "host": "::", "port": 31000,
                                             "enabled": False}))
        cls.app.call(manager.create_gateway({"name": "Encoded auth fixture", "host": "::", "port": 31001,
                                             "enabled": False, "username": "user@例", "password": "p:a/ss?#%"}))
        cls.server = cls.start_server("127.0.0.1")
        cls.secure_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.insecure_url = f"http://proxy-ui.test:{cls.server.server_port}"
        cls.ipv6_server = cls.start_server("::1")
        cls.ipv6_url = f"http://[::1]:{cls.ipv6_server.server_port}"
        cls.pw = sync_playwright().start()
        cls.resources.append(cls.pw.stop)
        cls.browser = cls.pw.chromium.launch(headless=True, args=[
            "--host-resolver-rules=MAP proxy-ui.test 127.0.0.1", "--no-proxy-server"])
        cls.resources.append(cls.browser.close)

    @classmethod
    def start_server(cls, host):
        server = ManagementServer((host, 0), cls.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(5)

        cls.resources.append(close)
        return server

    def setUp(self):
        self.errors = []
        self.context, self.page = self.new_page()
        self.addCleanup(lambda: self.assertEqual(self.errors, []))

    def new_page(self, mobile=False):
        context = self.browser.new_context(viewport={"width": 390 if mobile else 1280,
                                                     "height": 844 if mobile else 900},
                                           is_mobile=mobile, has_touch=mobile)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.secure_url)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.ipv6_url)
        self.addCleanup(context.close)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        return context, page

    def open_login(self, page=None, url=None):
        page = page or self.page
        page.goto(url or self.secure_url)
        expect(page.locator("#login-dialog")).to_be_visible()
        expect(page.locator("#login-submit")).to_be_enabled()

    def login(self, page=None, url=None):
        page = page or self.page
        self.open_login(page, url)
        page.get_by_label("用户名", exact=True).fill("fixture-user")
        page.get_by_label("密码", exact=True).fill("fixture-password")
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.locator("#login-dialog")).not_to_be_visible()
        expect(page.locator("#gateway-list")).to_contain_text("Copy fixture")

    def assert_copied(self, page, read_page, address):
        read_page.bring_to_front()
        self.assertEqual(read_page.evaluate("navigator.clipboard.readText()"), address)
        expect(page.locator("#copy-dialog")).not_to_be_visible()
        expect(page.locator("#toasts")).to_contain_text("代理地址已复制")
        self.assertEqual(page.locator("textarea").count(), 1)  # Existing config editor only.

    def test_login_compact_and_responsive(self):
        self.open_login()
        dialog = self.page.locator("#login-dialog")
        expect(dialog).not_to_contain_text("主机：")
        expect(dialog).not_to_contain_text("界面版本")
        expect(dialog).not_to_contain_text("admin / admin")
        for width, height in [(320, 568), (390, 844), (1280, 900)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": height})
                expect(dialog).to_be_visible()
                self.assertLessEqual(dialog.evaluate("el => el.scrollWidth"), dialog.evaluate("el => el.clientWidth"))
                box = dialog.bounding_box()
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width)
                self.assertGreaterEqual(self.page.locator("#login-submit").bounding_box()["height"], 44)
                for field in ["#login-username", "#login-password"]:
                    self.assertGreaterEqual(self.page.locator(field).evaluate("el => parseFloat(getComputedStyle(el).fontSize)"), 16)
                if ARTIFACTS:
                    self.page.screenshot(path=str(ARTIFACTS / f"login-{width}.png"))

    def test_login_error_recovery_reload_and_logout(self):
        self.open_login()
        self.page.locator("#login-username").fill("fixture-user")
        self.page.locator("#login-password").fill("wrong-password")
        self.page.locator("#login-submit").click()
        expect(self.page.locator("#login-error")).to_be_visible()
        expect(self.page.locator("#login-submit")).to_be_enabled()
        expect(self.page.locator("#login-password")).to_have_value("")
        self.page.locator("#login-password").fill("fixture-password")
        self.page.locator("#login-password").press("Enter")
        expect(self.page.locator("#login-dialog")).not_to_be_visible()
        self.page.reload()
        expect(self.page.locator("#gateway-list")).to_contain_text("Copy fixture")
        expect(self.page.locator("#login-dialog")).not_to_be_visible()
        self.page.get_by_role("button", name="退出", exact=True).click()
        expect(self.page.locator("#login-dialog")).to_be_visible()
        expect(self.page.locator("#workspace")).to_have_js_property("inert", True)

    def test_modern_clipboard_protocols_and_encoded_credentials(self):
        self.login()
        self.assertTrue(self.page.evaluate("isSecureContext"))
        self.page.evaluate("() => { document.execCommand = () => { throw new Error('unexpected compatibility copy'); }; }")
        css = self.page.locator('link[rel="stylesheet"]').get_attribute("href").split("?v=")[1]
        script = self.page.locator("script[src]").get_attribute("src").split("?v=")[1]
        self.assertEqual(css, script)
        expect(self.page.locator("[data-ui-version]")).to_have_text(f"界面版本 {script}")
        for name, port, prefix in [("Copy fixture", 31000, ""), ("Encoded auth fixture", 31001,
                                   f"{quote('user@例', safe='')}:{quote('p:a/ss?#%', safe='')}@")]:
            card = self.page.locator(".gateway-card").filter(has=self.page.get_by_role("heading", name=name, exact=True))
            for protocol in ["http", "https", "socks5"]:
                with self.subTest(name=name, protocol=protocol):
                    row = card.locator(f'[data-action="copy-{protocol}"]')
                    expect(row).to_have_js_property("tagName", "BUTTON")
                    row.locator("code").click()
                    self.assert_copied(self.page, self.page, f"{protocol}://{prefix}127.0.0.1:{port}")

    def test_plain_http_automatic_copy_desktop_and_touch(self):
        for mobile in [False, True]:
            with self.subTest(mobile=mobile):
                context, page = self.new_page(mobile)
                self.login(page, self.insecure_url)
                self.assertFalse(page.evaluate("isSecureContext"))
                self.assertFalse(page.evaluate("Boolean(navigator.clipboard)"))
                reader = context.new_page()
                reader.goto(self.secure_url)
                for protocol in ["http", "https", "socks5"]:
                    page.bring_to_front()
                    row = page.locator(".gateway-card").first.locator(f'[data-action="copy-{protocol}"]')
                    if mobile:
                        row.tap()
                    else:
                        row.click()
                    expect(page.locator("#toasts")).to_contain_text("代理地址已复制")
                    self.assert_copied(page, reader, f"{protocol}://proxy-ui.test:31000")
                    self.assertGreaterEqual(row.bounding_box()["height"], 44)
                if ARTIFACTS and mobile:
                    page.bring_to_front()
                    page.screenshot(path=str(ARTIFACTS / "copy-touch.png"))

    def test_permission_denied_uses_compatibility_copy(self):
        self.login()
        self.page.evaluate("""Object.defineProperty(navigator.clipboard, 'writeText', {
            value: async () => { throw new DOMException('fixture denied', 'NotAllowedError'); }
        })""")
        row = self.page.locator('[data-action="copy-http"]').first
        row.focus()
        row.press("Enter")
        expect(self.page.locator("#toasts")).to_contain_text("代理地址已复制")
        expect(row).to_be_focused()
        self.assert_copied(self.page, self.page, "http://127.0.0.1:31000")

    def test_ipv6_keyboard_copy_and_focus_scroll_restore(self):
        self.login(url=self.ipv6_url)
        self.page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: undefined})")
        reader = self.context.new_page()
        reader.goto(self.secure_url)
        self.page.bring_to_front()
        row = self.page.locator('[data-action="copy-socks5"]').first
        row.focus()
        self.page.evaluate("""() => {
            const range = document.createRange();
            range.selectNodeContents(document.querySelector('.gateway-card h3'));
            const selection = getSelection();
            selection.removeAllRanges(); selection.addRange(range);
        }""")
        selected = self.page.evaluate("getSelection().toString()")
        before = self.page.evaluate("[scrollX, scrollY]")
        row.press("Space")
        expect(self.page.locator("#toasts")).to_contain_text("代理地址已复制")
        expect(row).to_be_focused()
        self.assertEqual(self.page.evaluate("[scrollX, scrollY]"), before)
        self.assertEqual(self.page.evaluate("getSelection().toString()"), selected)
        self.assert_copied(self.page, reader, "socks5://[::1]:31000")

    def test_all_copy_methods_fail_without_false_success(self):
        self.login()
        self.page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: undefined})")
        for behavior in ["return false;", "throw new Error('fixture copy failed');"]:
            with self.subTest(behavior=behavior):
                self.page.evaluate(f"() => {{ document.execCommand = () => {{ {behavior} }}; }}")
                self.page.locator('[data-action="copy-https"]').first.click()
                expect(self.page.locator("#copy-dialog")).to_be_visible()
                expect(self.page.locator("#copy-value")).to_have_value("https://127.0.0.1:31000")
                expect(self.page.locator("#toasts")).not_to_contain_text("代理地址已复制")
                self.assertEqual(self.page.locator("textarea").count(), 1)
                self.assertEqual(self.page.locator("#copy-value").evaluate("el => [el.selectionStart, el.selectionEnd]"),
                                 [0, len("https://127.0.0.1:31000")])
                self.page.get_by_role("button", name="完成", exact=True).click()
                expect(self.page.locator("#copy-dialog")).not_to_be_visible()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path)
    args, unittest_args = parser.parse_known_args()
    ARTIFACTS = args.artifacts
    if ARTIFACTS:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *unittest_args])

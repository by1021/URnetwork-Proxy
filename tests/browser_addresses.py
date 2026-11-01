"""Real Chromium + local HTTP/TLS forwarding regression for adaptive addresses."""
import contextlib
import http.client
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.gateway import GatewayManager
from core.web_server import ManagementApp, ManagementServer
from tests.browser_smoke import BrowserClient, probe


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def forwarder(port):
    class Forwarder(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def forward(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            try:
                # Keep the browser's authority/origin, as in a Host-preserving proxy.
                conn.request(self.command, self.path, body=body, headers=dict(self.headers))
                response = conn.getresponse()
                data = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ('connection', 'transfer-encoding', 'server', 'date'):
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)
            finally:
                conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = forward
    return Forwarder


def check_page(browser, url, *, runtime_failure=False, remote=False, screenshot=None):
    from playwright.sync_api import expect
    with browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 1000}) as context:
        page = context.new_page()
        errors = []
        requests = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.on('request', lambda req: requests.append(req.url) if '/api/' in req.url else None)
        # Simulate a stale intermediary response on the legacy unversioned URL.
        page.route('**/app.js', lambda route: route.fulfill(
            content_type='text/javascript', body='window.legacyScriptLoaded = true;'))
        page.route('**/style.css', lambda route: route.fulfill(
            content_type='text/css', body='body { display: none; }'))
        if runtime_failure:
            page.route('**/api/runtime', lambda route: route.fulfill(
                status=503, content_type='application/json', body='{"error":"fixture runtime offline"}'))
        response = page.goto(url)
        assert response.status == 200
        host = page.evaluate('location.hostname')
        expect(page.locator('#login-dialog [data-ui-version]')).to_have_text('界面版本 20261031.4')
        assert page.evaluate('window.legacyScriptLoaded !== true')
        assert page.evaluate("performance.getEntriesByType('resource').some(entry => entry.name.endsWith('/app.js?v=20261031.4'))")
        assert page.evaluate("performance.getEntriesByType('resource').some(entry => entry.name.endsWith('/style.css?v=20261031.4'))")
        expect(page.locator('#login-dialog [data-access-host]')).to_have_text(host)
        expect(page.locator('#runtime-addresses')).to_have_text(url)
        page.locator('#login-username').fill('admin')
        page.locator('#login-password').fill('admin')
        page.locator('#login-submit').click()
        expect(page.locator('#login-dialog')).not_to_be_visible()
        expect(page.locator('.page-footer [data-ui-version]')).to_have_text('界面版本 20261031.4')
        expect(page.locator('.runtime-banner [data-access-host]')).to_have_text(host)
        expect(page.locator('#runtime-addresses')).to_have_text(url)
        expect(page.locator('.gateway-card')).to_have_count(1)
        for protocol in ('http', 'socks5'):
            row = page.locator('.address-row').filter(has=page.locator(f'[data-action="copy-{protocol}"]'))
            expect(row.locator('code')).to_have_text(f'{host}:10801')
            expect(row.locator('code')).to_have_attribute('title', f'{protocol}://{host}:10801')

        def check_copy(native=False):
            for protocol in ('http', 'socks5'):
                expected = f'{protocol}://{host}:10801'
                page.locator(f'[data-action="copy-{protocol}"]').click()
                if native:
                    page.wait_for_function('async expected => (await navigator.clipboard.readText()) === expected', arg=expected)
                    expect(page.locator('#copy-dialog')).not_to_be_visible()
                else:
                    expect(page.locator('#copy-dialog')).to_be_visible()
                    expect(page.locator('#copy-value')).to_have_value(expected)
                    page.locator('#copy-close').click()

        if page.evaluate('isSecureContext && Boolean(navigator.clipboard)'):
            context.grant_permissions(['clipboard-read', 'clipboard-write'])
            check_copy(native=True)
            # Permission denial must offer the same value through manual copy.
            page.evaluate("Object.defineProperty(navigator.clipboard, 'writeText', {value: async () => { throw new DOMException('Denied', 'NotAllowedError'); }})")
            check_copy()
        # HTTP on remote hosts has no Clipboard API; exercise this on every host.
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})")
        check_copy()
        expect(page.locator('.gateway-addresses')).to_contain_text('实际监听：127.0.0.1:10801')
        if remote:
            expect(page.locator('.gateway-addresses')).to_contain_text('连通性未验证')
            expect(page.locator('.gateway-addresses')).to_contain_text(host)
        if runtime_failure:
            expect(page.locator('#runtime-note')).to_contain_text('fixture runtime offline')
        else:
            expect(page.locator('#runtime-server-ips')).to_have_text('192.0.2.10')
        page.get_by_role('button', name='配置面板', exact=True).click()
        expect(page.locator('.config-facts [data-access-host]')).to_have_text(host)
        assert requests and all(value.startswith(url) for value in requests), requests
        page.get_by_role('button', name='网关总览', exact=False).first.click()
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors, errors
        print(f'PASS {url} runtime_failure={runtime_failure}')


def main():
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
        root = Path(directory)
        client = BrowserClient()
        manager = GatewayManager(client, root / 'gateways.json', probe=probe)
        app = ManagementApp({}, root, client=client, manager=manager, config_path=root / 'config.json')
        server = ManagementServer(('::', 0), app)
        app.runtime_ips = ['192.0.2.10']  # Deliberately unlike every browser entry point.
        app.start()
        stack.callback(app.close)
        stack.enter_context(running(server))
        app.call(manager.create_gateway(dict(name='地址测试', host='127.0.0.1', port=10801,
                                             mode='best', enabled=False)))
        proxy = stack.enter_context(running(ThreadingHTTPServer(('127.0.0.1', 0), forwarder(server.server_port))))
        cert, key = root / 'cert.pem', root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=console.urnetwork.test'], check=True, capture_output=True)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        https = ThreadingHTTPServer(('127.0.0.1', 0), forwarder(server.server_port))
        https.socket = tls.wrap_socket(https.socket, server_side=True)
        stack.enter_context(running(https))
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('CHROMIUM_PATH'),
                                        args=['--no-proxy-server', '--host-resolver-rules=MAP console.urnetwork.test 127.0.0.1'])
            try:
                base = f'http://127.0.0.1:{server.server_port}/'
                check_page(browser, base)
                check_page(browser, f'http://localhost:{server.server_port}/')
                if app.dual_stack:
                    check_page(browser, f'http://[::1]:{server.server_port}/')
                else:
                    print('SKIP actual IPv6: host has no dual-stack support')
                check_page(browser, f'http://console.urnetwork.test:{server.server_port}/', remote=True)
                check_page(browser, f'http://console.urnetwork.test:{proxy.server_port}/', remote=True)
                screenshot = Path(os.environ.get('SCREENSHOT_DIR', '/tmp/urnetwork-address-evidence')) / 'adaptive-address.png'
                check_page(browser, f'https://console.urnetwork.test:{https.server_port}/', remote=True, screenshot=screenshot)
                check_page(browser, base, runtime_failure=True)
            finally:
                browser.close()


if __name__ == '__main__':
    main()

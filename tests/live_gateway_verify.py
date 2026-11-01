"""Opt-in real URnetwork test. Creates and removes only its own temporary gateway."""
import argparse
import http.client
import ipaddress
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading

from core.config import load_config
from core.web_server import ManagementApp, ManagementServer
from tests.test_integration import free_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.parse_args()
    config = load_config(strict=True)
    with tempfile.TemporaryDirectory(prefix='urproxy-live-') as directory:
        root = Path(directory)
        app = ManagementApp(config, root, config_path=root / 'config.json')
        server = ManagementServer(('127.0.0.1', 0), app)
        app.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cookie = csrf = ''
        def request(method, path, data=None):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=150)
            try:
                conn.request(method, path, None if data is None else json.dumps(data), {
                    'Content-Type': 'application/json', 'Cookie': cookie, 'X-CSRF-Token': csrf})
                response = conn.getresponse()
                status, headers, body = response.status, dict(response.getheaders()), json.loads(response.read())
                if status != 200:
                    raise AssertionError(f'{method} {path}: HTTP {status}: {body.get("error")}')
                return headers, body
            finally:
                conn.close()
        gateway_id = None
        try:
            headers, _ = request('POST', '/api/login', dict(username=config['web_username'], password=config['web_password']))
            cookie = headers['Set-Cookie'].split(';')[0]
            csrf = request('GET', '/api/session')[1]['csrf']
            countries = request('GET', '/api/countries')[1]['countries']
            country = next(row for row in countries if row['code'] == 'US')
            print(f'PASS login and live countries ({len(countries)})', flush=True)
            password = secrets.token_hex(16)
            port = free_port()
            _, gateway = request('POST', '/api/gateways', dict(
                name='Temporary HTTPS verification', host='127.0.0.1', port=port,
                mode='country', country_code='US', location_id=country['location_id'],
                enabled=True, auto_heal=False, interval=300, username='verify', password=password))
            gateway_id = gateway['id']
            assert gateway['status'] == 'running', (gateway['status'], gateway['last_error'])
            print('PASS real US gateway created and healthy', flush=True)
            exits = []
            for protocol in ('http', 'https', 'socks5h'):
                command = ['curl', '--silent', '--show-error', '--fail', '--noproxy', '',
                           '--connect-timeout', '10', '--max-time', '30',
                           '--proxy', f'{protocol}://127.0.0.1:{port}',
                           '--proxy-user', f'verify:{password}']
                if protocol == 'https':
                    command += ['--proxy-cacert', str(config.get('tls_cert_file') or root / 'gateway_tls.crt')]
                result = subprocess.run(command + ['https://api.ipify.org'], capture_output=True, text=True, timeout=35)
                assert result.returncode == 0, f'{protocol}: curl exit {result.returncode}: {result.stderr}'
                exits.append(str(ipaddress.ip_address(result.stdout.strip())))
                print(f'PASS {protocol.upper()} -> HTTPS origin, certificate validation enabled', flush=True)
            _, checked = request('POST', f'/api/gateways/{gateway_id}/check', {})
            assert checked['status'] == 'running', checked['last_error']
            print(f'PASS active health check; consistent exit={len(set(exits)) == 1}', flush=True)
        finally:
            try:
                if gateway_id:
                    request('DELETE', f'/api/gateways/{gateway_id}', {})
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
                app.close()
            state = json.loads((root / 'gateways.json').read_text())
            assert not state['owned'], 'Temporary remote resource cleanup pending'
            print('PASS temporary local and remote resources removed', flush=True)


if __name__ == '__main__':
    main()

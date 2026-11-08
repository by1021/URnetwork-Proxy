"""Authentication/input/error contracts through a real management HTTP server."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch, MagicMock

from core.client import URClient, ApiError
from core.config import DEFAULT_CONFIG
from core.web_server import ManagementApp, ManagementServer


class LocationsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config={**DEFAULT_CONFIG,'web_username':'fixture-user','web_password':'fixture-password'}
        path=Path(self.tmp.name)/'config.json';path.write_text(json.dumps(config))
        self.client=URClient()
        self.calls=[]
        def request(path, method='GET', body=None, **kwargs):
            self.calls.append((path,body))
            return {'locations':[{'location_id':'us-ca','location_type':'region','name':'California','country_code':'us','region':'California','region_location_id':'us-ca','provider_count':5},
                {'location_id':'mx-ca','location_type':'region','name':'Baja California','country_code':'mx','region_location_id':'mx-ca','provider_count':10}]}
        self.client._request=request
        self.app=ManagementApp(config,self.tmp.name,client=self.client,config_path=path,web_host='127.0.0.1',web_port=0)
        self.app.start()
        self.server=ManagementServer(('127.0.0.1',0),self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.cookie=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5);self.app.close();self.tmp.cleanup()

    def request(self,path,body=None):
        headers={'Content-Type':'application/json'}
        if self.cookie:headers['Cookie']=self.cookie
        req=urllib.request.Request(self.base+path,headers=headers,data=None if body is None else json.dumps(body).encode())
        return urllib.request.urlopen(req,timeout=5)

    def login(self):
        with self.request('/api/login',{'username':'fixture-user','password':'fixture-password'}) as response:
            self.cookie=response.headers['Set-Cookie'].split(';',1)[0]

    def test_locations_requires_session_before_querying(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/locations?query=California&type=region')
        self.assertEqual(cm.exception.code,401);cm.exception.close()
        self.assertEqual(self.calls,[])

    def test_filter_and_input_validation_are_served(self):
        self.login()
        with self.request('/api/locations?query=California&type=region&country_code=US') as response:
            data=json.load(response)
        self.assertEqual([loc['location_id'] for loc in data['locations']],['us-ca'])
        self.assertFalse(data['stale'])
        count=len(self.calls)
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/locations?query=California&type=unsupported')
        self.assertEqual(cm.exception.code,400);cm.exception.close()
        self.assertEqual(len(self.calls),count)


class StructuredAPIErrorTests(unittest.TestCase):
    def test_malformed_error_code_is_sanitized(self):
        client = URClient(); client.jwt = 'test-fake-token'
        response = MagicMock(); response.__enter__.return_value = response
        response.read.return_value = b'{"error":{"code":[],"message":"test-fake-secret"}}'
        opener = MagicMock(); opener.open.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ApiError) as cm:
                client._request('/network/auth-client', method='POST', body={})
        self.assertEqual(cm.exception.code, 'rejected')
        self.assertNotIn('test-fake-secret', str(cm.exception))

    def test_renewable_get_does_not_send_the_error_body_on_retry(self):
        client = URClient(seedphrase='test-fake-seedphrase'); client.jwt = 'test-fake-token'
        client.login = MagicMock()
        error = urllib.error.HTTPError('http://fixture', 401, 'fixture', {},
            io.BytesIO(b'{"error":{"code":"authentication"}}'))
        response = MagicMock(); response.__enter__.return_value = response
        response.read.return_value = b'{}'
        opener = MagicMock(); opener.open.side_effect = [error, response]
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(client._request('/subscription/balance'), {})
        self.assertEqual(opener.open.call_count, 2)
        self.assertTrue(all(call.args[0].data is None for call in opener.open.call_args_list))

    def test_json_refusal_has_allowed_code_and_no_response_body(self):
        client=URClient();client.jwt='test-fake-token'
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=json.dumps({'error':{'no_unique_supply':True,'message':'test-fake-secret'}}).encode()
        opener=MagicMock();opener.open.return_value=response
        with patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaises(ApiError) as cm:client._request('/network/auth-client',method='POST',body={})
        self.assertEqual(cm.exception.code,'no_unique_supply')
        self.assertNotIn('test-fake-secret',str(cm.exception))
        self.assertEqual(opener.open.call_count,1)

    def test_http_refusal_preserves_retry_after_without_replay(self):
        client=URClient();client.jwt='test-fake-token'
        error=urllib.error.HTTPError('http://fixture',429,'fixture',{'Retry-After':'60'},io.BytesIO(b'{"error":{"rate_limited":true,"message":"test-fake-secret"}}'))
        opener=MagicMock();opener.open.side_effect=error
        with patch('urllib.request.build_opener',return_value=opener):
            with self.assertRaises(ApiError) as cm:client._request('/network/auth-client',method='POST',body={})
        self.assertEqual((cm.exception.code,cm.exception.retry_after),('rate_limited',60))
        self.assertEqual(opener.open.call_count,1)
        self.assertNotIn('test-fake-secret',str(cm.exception))


if __name__=='__main__':unittest.main()

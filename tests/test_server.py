"""HTTP smoke/security checks with no Windows dependencies (run on any OS)."""
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]

class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name)
        shutil.copy(ROOT / 'server.py', work / 'server.py')
        shutil.copy(ROOT / 'index.html', work / 'index.html')
        shutil.copytree(ROOT / 'static', work / 'static')
        cfg = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        cfg['port'] = cls.port
        (work / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        cls.process = subprocess.Popen([sys.executable, '-u', str(work / 'server.py')], cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if (work / 'config.json').exists():
                new = json.loads((work / 'config.json').read_text(encoding='utf-8'))
                if new.get('key'):
                    cls.key = new['key']
                    try:
                        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=1)
                        conn.request('GET','/api/config?k='+cls.key)
                        r=conn.getresponse();r.read();conn.close()
                        if r.status==200:break
                    except OSError:pass
            time.sleep(.1)
        else:raise RuntimeError('Test server did not start')
    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=8)
        cls.tmp.cleanup()
    def req(self, method, uri, headers=None):
        c=http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request(method,uri,headers=headers or {})
        r=c.getresponse();body=r.read();status=r.status;h=dict(r.getheaders());c.close()
        return status,body,h
    def test_token_generation(self):self.assertGreaterEqual(len(self.key),40)
    def test_unauthorized_api(self):self.assertEqual(self.req('GET','/api/config')[0],403)
    def test_authorized_api(self):
        status,data,_=self.req('GET','/api/config?k='+self.key)
        self.assertEqual(status,200)
        self.assertIn('buttons',json.loads(data))
    def test_static_resources(self):
        for url in ['/style.css','/app.js']:
            status,body,_=self.req('GET',url)
            self.assertEqual(status,200)
            self.assertGreater(len(body),100)
    def test_cross_site_post_rejected(self):
        status,_,_=self.req('POST','/api/press?i=0&k='+self.key, {'Origin':'http://evil.example'})
        self.assertEqual(status,403)
    def test_bad_group_and_negative_index(self):
        for query in ['i=-1','g=unknown&i=0','i=9000']:
            status,_,_=self.req('POST','/api/press?'+query+'&k='+self.key)
            self.assertEqual(status,500)
    def test_tomorrow_forecast_frontend(self):
        js=(ROOT/'static/app.js').read_text(encoding='utf-8')
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        self.assertIn('id="tomorrow"',html)
        self.assertIn('d.weather_code[1]',js)
        self.assertNotIn('id="worlds"',html)
    def test_security_headers(self):
        _,_,headers=self.req('GET','/?k='+self.key)
        self.assertEqual(headers.get('X-Frame-Options'),'DENY')
        self.assertEqual(headers.get('Referrer-Policy'),'no-referrer')

if __name__=='__main__':unittest.main()

# Additional v6 frontend checks are defined in test_media_ui.py.

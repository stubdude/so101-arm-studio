"""Local API boundaries; no motors, cameras, or robotics dependencies required."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from server import AppServer
from simulator import fake_factory, fake_ports


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.tmp.name, fake_factory, fake_ports)
        self.server = AppServer(('127.0.0.1',0),self.engine,True)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.host = f'127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.tmp.cleanup()

    def request(self, method, path, data=None, headers=None):
        c = http.client.HTTPConnection(self.host,timeout=3)
        c.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers or {})
        r = c.getresponse()
        result = r.status,r.read()
        c.close()
        return result

    def test_ui_and_demo_bootstrap_serve_without_ml_dependencies(self):
        self.assertEqual(self.request('GET','/')[0],200)
        status,body = self.request('GET','/api/bootstrap')
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['demo'])
        self.assertFalse(any(name.startswith('lerobot') for name in sys.modules))

    def test_external_origins_and_missing_tokens_cannot_issue_motor_actions(self):
        headers = {'Content-Type':'application/json','Origin':'https://untrusted.example','X-Control-Token':self.server.token}
        self.assertEqual(self.request('POST','/api/start',{},headers)[0],403)
        headers['Origin'] = 'http://' + self.host
        headers.pop('X-Control-Token')
        self.assertEqual(self.request('POST','/api/start',{},headers)[0],403)
        headers['X-Control-Token'] = self.server.token
        self.assertEqual(self.request('POST','/api/stop',{},headers)[0],202)
        self.assertEqual(self.engine.jobs.qsize(),1)

    def test_rebinding_host_and_file_traversal_are_rejected(self):
        self.assertEqual(self.request('GET','/api/bootstrap',headers={'Host':'untrusted.example'})[0],403)
        self.assertEqual(self.request('GET','/../engine.py')[0],404)
        self.assertEqual(self.request('GET','/api/session/not-a-session')[0],404)


if __name__ == '__main__':
    unittest.main()

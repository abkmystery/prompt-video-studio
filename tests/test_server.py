import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from server import StudioServer

class Bridge:
    def status(self): return {'available':True,'connected':False}
    def pending_approvals(self): return []
    def close(self): pass

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root/'web').mkdir()
        (cls.root/'web'/'index.html').write_text('<h1>Studio</h1>')
        cls.server = StudioServer(('127.0.0.1',0),root=cls.root,bridge=Bridge())
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.manager.close()
        cls.thread.join(2)
        cls.temp.cleanup()
    def request(self,method,path,data=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port)
        connection.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers or {})
        response=connection.getresponse()
        body=response.read()
        result=(response.status,dict(response.getheaders()),body)
        connection.close()
        return result
    def test_cross_origin_requests_are_denied(self):
        self.assertEqual(self.request('GET','/api/status',headers={'Origin':'https://evil.example'})[0],403)
    def test_rebinding_host_is_denied(self):
        self.assertEqual(self.request('GET','/api/status',headers={'Host':'evil.example'})[0],403)
    def test_csrf_required_for_mutations(self):
        self.assertEqual(self.request('POST','/api/jobs',{}, {'Content-Type':'application/json'})[0],403)
    def test_veo_is_removed(self):
        headers={'Content-Type':'application/json','X-Studio-Token':self.server.csrf}
        self.assertEqual(self.request('POST','/api/jobs',{'provider':'veo'},headers)[0],400)
    def test_invalid_duration_is_rejected(self):
        headers={'Content-Type':'application/json','X-Studio-Token':self.server.csrf}
        value={'provider':'codex','prompt':'A small animated film','duration':1201}
        self.assertEqual(self.request('POST','/api/jobs',value,headers)[0],400)
    def test_traversal_not_served(self):
        self.assertEqual(self.request('GET','/outputs/../server.py')[0],404)
    def test_video_supports_browser_range_requests(self):
        directory=self.root/'outputs'/'aaaaaaaaaaaaaaaa'
        directory.mkdir(exist_ok=True)
        (directory/'video.mp4').write_bytes(b'0123456789')
        status,headers,body=self.request('GET','/outputs/aaaaaaaaaaaaaaaa/video.mp4',headers={'Range':'bytes=3-6'})
        self.assertEqual((status,body),(206,b'3456'))
        self.assertEqual(headers['Content-Range'],'bytes 3-6/10')
    def test_status_does_not_expose_credentials(self):
        with patch.object(self.server.manager,'capabilities',return_value={'blender':True,'ffmpeg':True,'kokoro':True}):
            status,headers,body=self.request('GET','/api/status')
        value=json.loads(body)
        self.assertEqual(status,200)
        self.assertIn('csrf_token',value)
        self.assertNotIn('api_key',value)
    def test_approval_requires_explicit_boolean(self):
        headers={'Content-Type':'application/json','X-Studio-Token':self.server.csrf}
        self.assertEqual(self.request('POST','/api/approvals/test',{'approved':'yes'},headers)[0],400)

if __name__=='__main__': unittest.main()

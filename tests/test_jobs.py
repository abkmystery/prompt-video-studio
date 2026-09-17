import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.jobs import JobManager, validate_captions

class Bridge:
    def status(self): return {'connected':True}
    def pending_approvals(self): return []

class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.manager=JobManager(self.root,Bridge())
    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()
    def test_rejects_overlapping_captions(self):
        path = self.root/'captions.srt'
        path.write_text('1\n00:00:00,000 --> 00:00:02,000\nHello\n\n2\n00:00:01,500 --> 00:00:03,000\nAgain\n')
        with self.assertRaises(RuntimeError): validate_captions(path,30)
    def test_accepts_long_film_caption_timing(self):
        path = self.root/'captions.srt'
        path.write_text('1\n00:19:55,000 --> 00:19:58,000\nThe end.\n')
        self.assertEqual(validate_captions(path,1200),1)
    def test_twenty_minute_request_is_preserved(self):
        with patch.object(self.manager.executor,'submit'):
            job=self.manager.submit({'provider':'codex','prompt':'An original moral story with a consistent cast.', 'duration':1200,'narration':True})
        request=json.loads((self.root/'outputs'/job['id']/'request.json').read_text())
        self.assertEqual(request['duration'],1200)
        self.assertTrue(request['narration'])
        self.assertFalse(request['music'])
    def test_rejects_two_simultaneous_productions(self):
        with patch.object(self.manager.executor,'submit'):
            self.manager.submit({'prompt':'An original story.','duration':60})
            with self.assertRaises(ValueError):
                self.manager.submit({'prompt':'A second original story.','duration':60})
    def test_partial_mp4_is_not_advertised_as_complete(self):
        with patch.object(self.manager.executor,'submit'):
            job=self.manager.submit({'prompt':'An original story.','duration':60})
        (self.root/'outputs'/job['id']/'video.mp4').write_bytes(b'partial')
        self.assertIsNone(self.manager.get(job['id'])['video_url'])
    def test_thread_id_is_saved_for_resume(self):
        with patch.object(self.manager.executor,'submit'):
            job=self.manager.submit({'prompt':'An original story.','duration':60})
        self.manager._on_event(job['id'],{'type':'thread','thread_id':'saved-thread'})
        saved=json.loads((self.root/'outputs'/job['id']/'job.json').read_text())
        self.assertEqual(saved['thread_id'],'saved-thread')
    def test_restart_recovers_interrupted_state(self):
        with patch.object(self.manager.executor,'submit'):
            job=self.manager.submit({'prompt':'An original story.','duration':60})
        other=JobManager(self.root,Bridge())
        try:
            restored=other.get(job['id'])
            self.assertEqual(restored['status'],'interrupted')
            self.assertTrue(restored['can_resume'])
        finally: other.close()

    def test_legacy_job_defaults_to_video_and_manual_approval(self):
        directory=self.root/'outputs'/('a'*16)
        directory.mkdir(parents=True)
        (directory/'job.json').write_text(json.dumps({'id':'a'*16,'provider':'codex','status':'completed','created_at':'2026-01-01','duration':60}))
        other=JobManager(self.root,Bridge())
        try:
            restored=other.get('a'*16)
            self.assertEqual((restored['kind'],restored['approval_mode']),('video','manual'))
        finally: other.close()

    def test_extend_uses_measured_source_duration_and_new_job(self):
        self.manager.jobs['a'*16]={'id':'a'*16,'provider':'codex','kind':'video','status':'completed','created_at':'2026-01-01',
            'duration':60,'verification':{'duration_seconds':8.0},'prompt':'source'}
        source=self.root/'outputs'/('a'*16)
        source.mkdir(parents=True)
        (source/'video.mp4').write_bytes(b'original bytes')
        with patch.object(self.manager.executor,'submit'):
            job=self.manager.submit({'prompt':'Continue the existing action naturally.','duration':30,'operation':'extend','source_job_id':'a'*16})
        self.assertNotEqual(job['id'],'a'*16)
        request=json.loads((self.root/'outputs'/job['id']/'request.json').read_text())
        self.assertEqual(request['duration'],38.0)
        self.assertEqual((self.root/'outputs'/job['id']/'source'/'job'/'video.mp4').read_bytes(),b'original bytes')

    def test_asset_job_copies_base_asset_without_mutating_it(self):
        asset_dir=self.root/'outputs'/('b'*16)
        asset_dir.mkdir(parents=True)
        import base64,struct
        payload=json.dumps({'asset':{'version':'2.0'},'scenes':[{'nodes':[0]}],'nodes':[{}]},separators=(',',':')).encode()
        payload += b' ' * (-len(payload)%4)
        (asset_dir/'asset.glb').write_bytes(b'glTF'+struct.pack('<II',2,20+len(payload))+struct.pack('<II',len(payload),0x4E4F534A)+payload)
        (asset_dir/'preview.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
        (asset_dir/'asset.json').write_text('{"type":"character"}')
        (asset_dir/'credits.md').write_text('Original')
        self.manager.library.publish(asset_dir,{'job_id':'b'*16,'asset_type':'character','name':'Hero','prompt':'Hero','style':'anime'})
        before=(self.root/'library'/('b'*16)/'asset.glb').read_bytes()
        with patch.object(self.manager.executor,'submit'):
            job=self.manager.submit({'kind':'asset','asset_type':'character','name':'Hero v2','base_asset_id':'b'*16,
                'prompt':'Create a revised hero with a blue jacket.','style':'anime'})
        copied=self.root/'outputs'/job['id']/'references'/'assets'/('b'*16)/'asset.glb'
        self.assertEqual(copied.read_bytes(),before)
        copied.write_bytes(b'changed copy')
        self.assertEqual((self.root/'library'/('b'*16)/'asset.glb').read_bytes(),before)

if __name__=='__main__': unittest.main()

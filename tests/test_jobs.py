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

if __name__=='__main__': unittest.main()

"""Validation, preservation, cancellation, and local audio regression tests."""
from array import array
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from studio import local_render as renderer
from studio import production_tools as production

class LocalRenderTests(unittest.TestCase):
    def test_demo_duration_and_copy(self):
        for length in (8,16,24):
            original=renderer.demo_plan(length)
            plan=renderer.validate_plan(original,duration=length)
            self.assertEqual(sum(s['duration'] for s in plan['scenes']),length)
            plan['scenes'][0]['characters'][0]['name']='Changed'
            self.assertEqual(original['scenes'][0]['characters'][0]['name'],'Amal')

    def test_untrusted_and_unsupported_plans_are_rejected(self):
        changes=[lambda p:p.update(code='print(1)'),lambda p:p.update(duration=True),
                 lambda p:p.update(duration=1200),lambda p:p.update(scenes=[]),
                 lambda p:p['scenes'][0].update(duration=7),
                 lambda p:p['scenes'][0].update(action='execute'),
                 lambda p:p['scenes'][0].update(setting='../../elsewhere'),
                 lambda p:p['scenes'][0].update(palette=['mint']),
                 lambda p:p['scenes'][0].update(characters=p['scenes'][0]['characters'][:1]),
                 lambda p:p['scenes'][0]['characters'][0].update(outfit='__import__(os)'),
                 lambda p:p.update(narration='word '*25),lambda p:p.update(title='bad\x00title')]
        for change in changes:
            with self.subTest(change=change):
                plan=renderer.demo_plan();change(plan)
                with self.assertRaises(ValueError):renderer.validate_plan(plan)
        with self.assertRaisesRegex(ValueError,'requested duration'):
            renderer.validate_plan(renderer.demo_plan(8),duration=16)

    def test_existing_project_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'project.blend';path.write_bytes(b'user project')
            with patch.object(renderer,'discover_tools',return_value={'available':True}):
                with self.assertRaisesRegex(ValueError,'not be overwritten'):
                    renderer.render(renderer.demo_plan(),Path(folder),'portrait',lambda *a:None,threading.Event())
            self.assertEqual(path.read_bytes(),b'user project')

    def test_precancelled_job_starts_no_process(self):
        event=threading.Event();event.set()
        with tempfile.TemporaryDirectory() as folder,patch.object(renderer.subprocess,'Popen') as process:
            with self.assertRaises(renderer.RenderCancelled):renderer._run(['blender'],Path(folder),'run.log',event)
            process.assert_not_called()

    def test_caption_text_cannot_inject_ass_directives(self):
        plan=renderer.demo_plan();plan['scenes'][0]['caption']='Hello {\\pos(0,0)} world'
        with tempfile.TemporaryDirectory() as folder:
            renderer._caption_files(plan,Path(folder),360,640)
            content=(Path(folder)/'captions.ass').read_text(encoding='utf-8')
            self.assertNotIn('{\\pos',content)
            self.assertIn('Hello ( pos(0,0)) world',content)


    def test_ffmpeg_discovery_with_redirected_localappdata(self):
        with tempfile.TemporaryDirectory() as folder:
            user=Path(folder)/'user'
            expected=user/'AppData'/'Local'/'Microsoft'/'WinGet'/'Packages'/'Gyan.FFmpeg_test'/'ffmpeg-test'/'bin'/'ffmpeg.exe'
            expected.parent.mkdir(parents=True)
            expected.write_bytes(b'test fixture; never executed')
            redirected=Path(folder)/'sandbox-local'
            overrides={'LOCALAPPDATA':str(redirected),'FFMPEG_PATH':'','BLENDER_PATH':'','PROGRAMFILES':str(Path(folder)/'programs')}
            with patch.object(renderer.Path,'home',return_value=user), patch.object(renderer.Path,'glob',return_value=[]), patch.object(renderer.shutil,'which',return_value=None), patch.dict(renderer.os.environ,overrides):
                found=renderer.discover_tools()
                self.assertEqual(found['ffmpeg'],str(expected.resolve()))
                self.assertEqual(renderer.os.environ['LOCALAPPDATA'],str(redirected))

class ProductionHelperTests(unittest.TestCase):
    def test_text_chunks_preserve_words_and_bound_size(self):
        text=' '.join('word'+str(i) for i in range(200))+'. Second sentence.'
        chunks=list(production._chunks(text))
        self.assertTrue(all(0<len(c)<=380 for c in chunks))
        self.assertEqual(' '.join(chunks),text)

    def test_ambient_exact_stereo_duration_and_no_clipping(self):
        with tempfile.TemporaryDirectory() as folder:
            result=production.ambient(Path(folder)/'wind.wav',.25,seed=11)
            with wave.open(str(result),'rb') as wav:
                self.assertEqual((wav.getnchannels(),wav.getframerate(),wav.getnframes()),(2,24000,6000))
                samples=array('h',wav.readframes(wav.getnframes()))
            self.assertGreater(max(abs(x) for x in samples),0)
            self.assertLess(max(abs(x) for x in samples),32767)
            self.assertIn('No music',json.loads(result.with_suffix('.audio.json').read_text())['description'])
            with self.assertRaisesRegex(ValueError,'already exists'):production.ambient(result,.25)

    def test_invalid_ambient_creates_no_file(self):
        for duration in (0,-1,7201,float('nan')):
            with self.subTest(duration=duration),tempfile.TemporaryDirectory() as folder:
                target=Path(folder)/'invalid.wav'
                with self.assertRaises(ValueError):production.ambient(target,duration)
                self.assertFalse(target.exists())

    def test_long_film_caption_timestamp(self):
        self.assertEqual(production._stamp(1199.125),'00:19:59,125')
        self.assertEqual(production._stamp(3600.1),'01:00:00,100')

    def test_missing_assets_report_unavailable_without_download(self):
        with patch.object(production,'_first',return_value=None):found=production.discover_audio()
        self.assertFalse(found['available']);self.assertFalse(found['downloads_required'])

if __name__=='__main__':unittest.main()

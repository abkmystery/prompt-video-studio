"""Fresh-install discovery without executing external programs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from studio import local_render, production_tools


class ToolDiscoveryTests(unittest.TestCase):
    def test_portable_blender_in_generic_downloads_locations(self):
        for relative in ('Downloads/blender-5.2-windows-x64/blender.exe',
                         'Downloads/Blender Tools/blender-5.2-windows-x64/blender.exe'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as folder:
                user = Path(folder)
                expected = user / relative
                expected.parent.mkdir(parents=True)
                expected.write_bytes(b'fixture; never executed')
                overrides = {'BLENDER_PATH': '', 'FFMPEG_PATH': '', 'PROGRAMFILES': str(user/'programs')}
                with patch.object(local_render.Path, 'home', return_value=user), \
                     patch.object(local_render.Path, 'glob', return_value=[]), \
                     patch.object(local_render.shutil, 'which', return_value=None), \
                     patch.dict(local_render.os.environ, overrides):
                    self.assertEqual(local_render.discover_tools()['blender'], str(expected.resolve()))

    def test_audio_assets_use_generic_downloads_and_explicit_python(self):
        with tempfile.TemporaryDirectory() as folder:
            user = Path(folder)
            audio = user/'Downloads'/'kokoro'
            for relative in ('models/kokoro-v1.0.onnx', 'models/voices-v1.0.bin', 'python.exe'):
                path = audio/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture; never executed')
            (audio/'packages').mkdir()
            overrides = {name: '' for name in ('STUDIO_AUDIO_ROOT', 'KOKORO_MODEL_PATH',
                'KOKORO_VOICES_PATH', 'KOKORO_PACKAGES_PATH')}
            overrides['KOKORO_PYTHON'] = str(audio/'python.exe')
            with patch.object(production_tools.Path, 'home', return_value=user), \
                 patch.object(production_tools, 'APP_ROOT', user/'app'), \
                 patch.dict(production_tools.os.environ, overrides):
                found = production_tools.discover_audio()
            self.assertTrue(found['available'])
            self.assertEqual(found['model'], str((audio/'models'/'kokoro-v1.0.onnx').resolve()))
            self.assertEqual(found['python'], str((audio/'python.exe').resolve()))


if __name__ == '__main__':
    unittest.main()

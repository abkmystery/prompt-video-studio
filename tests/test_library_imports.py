import base64
import io
import json
import tempfile
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.imports import ImportStore
from studio.library import AssetLibrary, _valid_model


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")

def glb():
    payload=json.dumps({'asset':{'version':'2.0'},'scenes':[{'nodes':[0]}],'nodes':[{'name':'Hero'}]},separators=(',',':')).encode()
    payload += b' ' * (-len(payload)%4)
    return b'glTF'+struct.pack('<II',2,20+len(payload))+struct.pack('<II',len(payload),0x4E4F534A)+payload


class LibraryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = AssetLibrary(self.root)
        self.imports = ImportStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def asset_job(self, asset_id="a" * 16):
        directory = self.root / "outputs" / asset_id
        directory.mkdir(parents=True)
        (directory / "asset.glb").write_bytes(glb())
        (directory / "preview.png").write_bytes(PNG)
        (directory / "asset.json").write_text(json.dumps({"type":"character","description":"A hero"}),encoding="utf-8")
        (directory / "credits.md").write_text("Original work",encoding="utf-8")
        return directory

    def test_asset_publication_is_copied_and_tamper_is_not_reused(self):
        directory = self.asset_job()
        request = {"job_id":"a"*16,"asset_type":"character","name":"Hero","prompt":"A hero","style":"anime"}
        result = self.library.publish(directory,request)
        original_hash = result["files"]["asset.glb"]["sha256"]
        (directory / "asset.glb").write_bytes(b"changed source")
        self.assertEqual(self.library.get("a"*16)["files"]["asset.glb"]["sha256"],original_hash)
        (self.root / "library" / ("a"*16) / "asset.glb").write_bytes(b"tampered")
        with self.assertRaises(KeyError):
            self.library.get("a"*16)

    def test_asset_publication_rejects_fake_preview_and_bad_model(self):
        directory = self.asset_job("b"*16)
        request = {"job_id":"b"*16,"asset_type":"character","name":"Hero","prompt":"A hero","style":"anime"}
        (directory / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x"*100)
        with self.assertRaisesRegex(RuntimeError,"preview"):
            self.library.publish(directory,request)
        (directory / "preview.png").write_bytes(PNG)
        (directory / "asset.glb").write_bytes(b"not really a glb file")
        with self.assertRaisesRegex(RuntimeError,"asset.blend"):
            self.library.publish(directory,request)

    def test_asset_publication_rejects_untrusted_job_identity(self):
        directory = self.asset_job("c"*16)
        request = {"job_id":"../../outside","asset_type":"character","name":"Hero","prompt":"A hero","style":"anime"}
        with self.assertRaisesRegex(RuntimeError,"identity"):
            self.library.publish(directory,request)
        self.assertFalse((self.root.parent/'outside').exists())

    def test_script_upload_is_fixed_name_and_rejects_binary_and_traversal(self):
        saved = self.imports.save(io.BytesIO(b"A safe script"),13,"script","story.md")
        self.assertEqual(saved["filename"],"source.md")
        with self.assertRaises(ValueError):
            self.imports.save(io.BytesIO(b"x"),1,"script","../story.md")
        with self.assertRaises(ValueError):
            self.imports.save(io.BytesIO(b"bad\x00data"),8,"script","bad.txt")
        self.assertFalse(any(path.name.endswith('.tmp') for path in self.imports.directory.iterdir()))

    def test_truncated_upload_is_removed(self):
        with self.assertRaisesRegex(ValueError,"ended"):
            self.imports.save(io.BytesIO(b"short"),10,"script","story.txt")
        self.assertEqual(list(self.imports.directory.iterdir()),[])

    def test_video_probe_uses_protocol_and_format_allowlists(self):
        fake = self.root / "ffprobe"
        fake.write_bytes(b"x")
        result = type("Result",(),{"returncode":0,"stdout":json.dumps({"format":{"duration":"8.25","format_name":"mov,mp4,m4a,3gp,3g2,mj2"},"streams":[{"codec_type":"video"}]})})()
        with patch("studio.local_render.discover_tools",return_value={"ffmpeg":str(self.root/"ffmpeg")}), \
             patch("studio.imports.subprocess.run",return_value=result) as run:
            value = self.imports.save(io.BytesIO(b"video bytes"),11,"video","clip.mp4")
        self.assertEqual(value["duration_seconds"],8.25)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-protocol_whitelist")+1],"file,pipe")
        self.assertIn("-format_whitelist",command)

    def test_compressed_blend_is_accepted_only_after_safe_blender_open(self):
        path=self.root/'asset.blend'
        path.write_bytes(b'\x28\xb5\x2f\xfd'+b'compressed blend data')
        result=type('Result',(),{'returncode':0})()
        with patch('studio.local_render.discover_tools',return_value={'blender':str(self.root/'blender')}), \
             patch('studio.library.subprocess.run',return_value=result) as run:
            self.assertTrue(_valid_model(path))
        command=run.call_args.args[0]
        self.assertIn('--disable-autoexec',command)
        self.assertIn('--python-exit-code',command)


if __name__ == "__main__":
    unittest.main()

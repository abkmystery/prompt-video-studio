import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts' / 'build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class ReleaseTests(unittest.TestCase):
    def make_source(self, root):
        for name in builder.REQUIRED:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('public source\n', encoding='utf-8')
        return root

    def test_allowlist_excludes_runtime_and_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_source(Path(temp) / 'source')
            for name in ['outputs/job/video.mp4', '.venv/python.exe', '.env', 'auth.json',
                         'tools/tool.exe', 'assets/model.onnx', 'server.log', 'docs/private.txt',
                         'studio/secret.py', 'dist/old.zip']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('do not publish', encoding='utf-8')
            asset = root / 'docs' / 'media' / 'demo.mp4'
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(b'public-demo')
            archive, checksum = builder.build_release(root, Path(temp) / 'out', '0.1.0')
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                expected = {f'prompt-video-studio-0.1.0/{name}' for name in builder.REQUIRED}
                expected.add('prompt-video-studio-0.1.0/docs/media/demo.mp4')
                self.assertEqual(names, expected)
                self.assertEqual(package.testzip(), None)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), checksum)
            self.assertIn(checksum, archive.with_suffix('.zip.sha256').read_text())

    def test_reproducible_and_normalizes_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_source(Path(temp) / 'source')
            first, _ = builder.build_release(root, Path(temp) / 'a', '0.1.0')
            for name in builder.REQUIRED:
                (root / name).write_bytes(b'public source\r\n')
                os.utime(root / name, (1800000000, 1800000000))
            second, _ = builder.build_release(root, Path(temp) / 'b', '0.1.0')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as package:
                self.assertEqual(package.read('prompt-video-studio-0.1.0/Start Studio.cmd'), b'public source\r\n')

    def test_missing_required_file_and_unsafe_version_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'missing'):
                builder.build_release(Path(temp), Path(temp) / 'out', '0.1.0')
            with self.assertRaisesRegex(ValueError, 'semantic'):
                builder.build_release(Path(temp), Path(temp) / 'out', '../escape')

    def test_repository_has_required_files(self):
        names = {path.relative_to(ROOT).as_posix() for path in builder.release_files(ROOT)}
        self.assertTrue(set(builder.REQUIRED).issubset(names))

@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows launcher checks')
class WindowsLauncherTests(unittest.TestCase):
    def run_ps(self, script):
        helper = str(ROOT / 'scripts' / 'windows_common.ps1').replace("'", "''")
        process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
                                  "$ErrorActionPreference='Stop'; . '" + helper + "'; " + script],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout.strip()

    def test_path_comparison_handles_case_and_rejects_other_folders(self):
        result = self.run_ps("@( (Test-StudioSamePath 'C:\\Users\\Test\\Studio\\outputs\\' 'c:\\users\\test\\studio\\outputs'), (Test-StudioSamePath 'C:\\Studio\\outputs' 'C:\\Other\\outputs') ) | ConvertTo-Json -Compress")
        self.assertEqual(result, '[true,false]')

    def test_running_foreign_copy_is_refused(self):
        script = "function Invoke-RestMethod { param($Uri,$TimeoutSec); if ($Uri -like '*/api/health') { return @{app='Prompt Video Studio'} }; return @{output_dir='C:\\Other\\outputs';csrf_token='never-print'} }; (Get-StudioInstance -Root 'C:\\Release').State"
        self.assertEqual(self.run_ps(script), 'foreign')

    def test_running_current_copy_is_recognized(self):
        script = "function Invoke-RestMethod { param($Uri,$TimeoutSec); if ($Uri -like '*/api/health') { return @{app='Prompt Video Studio'} }; return @{output_dir='C:\\Release\\outputs';csrf_token='never-print'} }; (Get-StudioInstance -Root 'C:\\Release').State"
        self.assertEqual(self.run_ps(script), 'current')

    def test_occupied_unknown_port_is_refused(self):
        script = "function Invoke-RestMethod { throw 'No health endpoint' }; function Test-StudioPort { return $true }; (Get-StudioInstance -Root 'C:\\Release').State"
        self.assertEqual(self.run_ps(script), 'foreign')

    def test_python_detection_accepts_supported_interpreter(self):
        import sys
        python = sys.executable.replace("'", "''")
        self.assertEqual(self.run_ps("Find-StudioPython -PythonPath '" + python + "'"), sys.executable)

    def test_scripts_parse_with_windows_powershell(self):
        paths = [ROOT / name for name in ['setup.ps1', 'launch.ps1', 'stop.ps1', 'scripts/windows_common.ps1']]
        script = '$issues = @(); '
        for path in paths:
            escaped = str(path).replace("'", "''")
            script += "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile('" + escaped + "',[ref]$tokens,[ref]$errors) | Out-Null; $issues += $errors; "
        script += 'if ($issues.Count) { throw ($issues | Out-String) }; Write-Output parsed'
        self.assertEqual(self.run_ps(script), 'parsed')

if __name__ == '__main__':
    unittest.main()

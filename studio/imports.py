from __future__ import annotations

import json
import math
import re
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .common import atomic_json


LIMITS = {"video": 512 * 1024 * 1024, "script": 1024 * 1024}
EXTENSIONS = {"video": {".mp4", ".webm", ".mov"}, "script": {".txt", ".md", ".srt"}}


def now():
    return datetime.now(timezone.utc).isoformat()


def _display_name(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
        raise ValueError("Choose a file with a name shorter than 200 characters.")
    name = value.strip()
    if any(ord(char) < 32 for char in name) or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("The upload name contains unsupported characters.")
    return name


class ImportStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        directory = self.root / "imports"
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory.resolve(strict=True)
        if not self.directory.is_relative_to(self.root):
            raise RuntimeError("Imports must stay inside the studio folder.")

    def _path(self, import_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{16}", import_id):
            raise KeyError("Import not found.")
        return self.directory / import_id

    def _load(self, import_id: str) -> dict:
        directory = self._path(import_id)
        try:
            metadata = directory / "import.json"
            if directory.is_symlink() or metadata.is_symlink() or not directory.resolve(strict=True).is_relative_to(self.directory):
                raise ValueError
            record = json.loads(metadata.read_text(encoding="utf-8-sig"))
            if record.get("id") != import_id or record.get("kind") not in LIMITS:
                raise ValueError
            filename = record.get("filename")
            if not isinstance(filename, str) or filename != Path(filename).name:
                raise ValueError
            file = directory / filename
            if file.is_symlink() or not file.is_file() or file.stat().st_size != record.get("bytes"):
                raise ValueError
            return record
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise KeyError("Import not found.") from None

    def public(self, record: dict) -> dict:
        result = dict(record)
        result["url"] = f"/imports/{record['id']}/{record['filename']}"
        return result

    def list(self) -> list[dict]:
        records = []
        for directory in self.directory.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                try:
                    records.append(self.public(self._load(directory.name)))
                except KeyError:
                    continue
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def get(self, import_id: str, expected_kind: str | None = None) -> dict:
        record = self._load(import_id)
        if expected_kind and record["kind"] != expected_kind:
            raise ValueError(f"Choose a {expected_kind} import.")
        return self.public(record)

    def file(self, import_id: str, name: str) -> Path:
        record = self._load(import_id)
        if name != record["filename"]:
            raise KeyError("Import file not found.")
        return self.directory / import_id / name

    def save(self, stream, content_length: int, kind: str, name: str) -> dict:
        if kind not in LIMITS:
            raise ValueError("Import kind must be video or script.")
        display_name = _display_name(name)
        extension = Path(display_name).suffix.lower()
        if extension not in EXTENSIONS[kind]:
            allowed = ", ".join(sorted(EXTENSIONS[kind]))
            raise ValueError(f"Choose a supported {kind} file ({allowed}).")
        limit = LIMITS[kind]
        if type(content_length) is not int or not 1 <= content_length <= limit:
            raise ValueError(f"The {kind} import must be between 1 byte and {limit} bytes.")
        import_id = secrets.token_hex(8)
        temporary = self.directory / f".{import_id}.{secrets.token_hex(4)}.tmp"
        temporary.mkdir()
        filename = f"source{extension}"
        file = temporary / filename
        remaining = content_length
        try:
            with file.open("xb") as target:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("The upload ended before the declared size was received.")
                    target.write(chunk)
                    remaining -= len(chunk)
            if kind == "script":
                duration = None
                self._validate_script(file)
            else:
                duration = self._probe_video(file)
            record = {"id": import_id, "kind": kind, "name": display_name, "filename": filename,
                      "bytes": content_length, "duration_seconds": duration, "created_at": now()}
            atomic_json(temporary / "import.json", record)
            temporary.replace(self.directory / import_id)
            return self.public(record)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    @staticmethod
    def _validate_script(path: Path):
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            raise ValueError("Scripts must be plain UTF-8 text.") from None
        if not content.strip() or "\x00" in content:
            raise ValueError("Scripts must contain plain UTF-8 text.")
        controls = sum(1 for char in content if ord(char) < 32 and char not in "\r\n\t")
        if controls > max(2, len(content) // 100):
            raise ValueError("The script appears to contain binary data.")

    @staticmethod
    def _probe_video(path: Path) -> float:
        from .local_render import discover_tools
        ffmpeg = discover_tools().get("ffmpeg")
        if not ffmpeg:
            raise ValueError("FFmpeg is required to inspect imported video.")
        probe = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe")
        if not probe.is_file():
            raise ValueError("ffprobe was not found next to FFmpeg.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run([str(probe), "-v", "error", "-protocol_whitelist", "file,pipe",
                                 "-format_whitelist", "mov,matroska,webm", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_type:format=duration,format_name",
                                 "-of", "json", str(path)], capture_output=True, text=True,
                                timeout=30, creationflags=flags)
        if result.returncode:
            raise ValueError("The uploaded video could not be read.")
        try:
            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])
            formats = set(str(data["format"].get("format_name","")).split(','))
            allowed = {'.mp4':{'mov','mp4','m4a','3gp','3g2','mj2'},'.mov':{'mov','mp4','m4a','3gp','3g2','mj2'},
                       '.webm':{'matroska','webm'}}[path.suffix.lower()]
            if (not math.isfinite(duration) or duration <= 0 or duration > 1200 or not formats.intersection(allowed)
                    or not any(s.get("codec_type") == "video" for s in data.get("streams", []))):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("The uploaded video must contain up to 20 minutes of readable video.") from None
        return round(duration, 3)

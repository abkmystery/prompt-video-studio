from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import struct
import subprocess
import zlib
from datetime import datetime, timezone
from pathlib import Path

from .common import atomic_json


ASSET_TYPES = {"character", "object", "scene"}
MODEL_FILES = {"asset.blend", "asset.glb"}
PUBLISHED_FILES = MODEL_FILES | {"preview.png", "credits.md"}


def now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _valid_png(path: Path) -> bool:
    if not path.is_file() or path.is_symlink() or not 45 <= path.stat().st_size <= 20 * 1024 * 1024:
        return False
    try:
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        offset, chunks, compressed = 8, [], bytearray()
        while offset + 12 <= len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            kind = data[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(data):
                return False
            payload = data[offset + 8:offset + 8 + length]
            expected = struct.unpack(">I", data[offset + 8 + length:end])[0]
            if zlib.crc32(kind + payload) & 0xffffffff != expected:
                return False
            chunks.append((kind,payload))
            if kind == b"IDAT":
                compressed.extend(payload)
            offset = end
            if kind == b"IEND":
                break
        if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13 or chunks[-1][0] != b"IEND" or offset != len(data):
            return False
        width,height = struct.unpack(">II",chunks[0][1][:8])
        inflater = zlib.decompressobj()
        decoded = inflater.decompress(bytes(compressed),64 * 1024 * 1024 + 1)
        return (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 64_000_000
                and bool(compressed) and 0 < len(decoded) <= 64 * 1024 * 1024 and inflater.eof)
    except (OSError,ValueError,struct.error,zlib.error):
        return False


def _valid_model(path: Path) -> bool:
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 20:
        return False
    data = path.read_bytes() if path.suffix == '.glb' and path.stat().st_size <= 512 * 1024 * 1024 else None
    if path.name == "asset.blend":
        with path.open('rb') as stream:
            header = stream.read(12)
        plain = header.startswith(b"BLENDER") and header[7:8] in {b'_',b'-'} and header[8:9] in {b'v',b'V'} and header[9:12].isdigit()
        compressed = header.startswith(b'\x28\xb5\x2f\xfd') or header.startswith(b'\x1f\x8b')
        if not (plain or compressed):
            return False
        try:
            from .local_render import discover_tools
            blender = discover_tools().get('blender')
            if not blender:
                return False
            flags = getattr(subprocess,'CREATE_NO_WINDOW',0)
            result = subprocess.run([str(blender),'--background','--disable-autoexec',str(path),'--python-exit-code','7',
                                     '--python-expr','import bpy; assert len(bpy.data.objects) > 0'],
                                    capture_output=True,timeout=60,creationflags=flags)
            return result.returncode == 0
        except (OSError,subprocess.TimeoutExpired):
            return False
    if not data or data[:4] != b"glTF" or int.from_bytes(data[4:8],"little") != 2 or int.from_bytes(data[8:12],"little") != len(data):
        return False
    try:
        offset, document = 12, None
        while offset + 8 <= len(data):
            length, kind = struct.unpack('<II',data[offset:offset+8])
            end = offset + 8 + length
            if end > len(data) or length == 0:
                return False
            payload = data[offset+8:end]
            if document is None:
                if kind != 0x4E4F534A:
                    return False
                document = json.loads(payload.rstrip(b' \x00').decode('utf-8'))
            offset = end
        if offset != len(data) or not isinstance(document,dict) or document.get('asset',{}).get('version') != '2.0':
            return False
        if not any(document.get(key) for key in ('scenes','nodes','meshes')):
            return False
        for collection in ('buffers','images'):
            if any(isinstance(item,dict) and item.get('uri') for item in document.get(collection,[])):
                return False
        return True
    except (UnicodeError,ValueError,TypeError,struct.error,json.JSONDecodeError):
        return False


class AssetLibrary:
    def __init__(self, root: Path):
        self.root = root.resolve()
        directory = self.root / "library"
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory.resolve(strict=True)
        if not self.directory.is_relative_to(self.root):
            raise RuntimeError("The asset library must stay inside the studio folder.")

    def _record_path(self, asset_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{16}", asset_id):
            raise KeyError("Asset not found.")
        return self.directory / asset_id / "asset.json"

    def _load(self, asset_id: str) -> dict:
        path = self._record_path(asset_id)
        try:
            if path.is_symlink():
                raise ValueError
            if not path.parent.resolve(strict=True).is_relative_to(self.directory):
                raise ValueError
            record = json.loads(path.read_text(encoding="utf-8-sig"))
            if record.get("id") != asset_id or record.get("kind") != "asset" or record.get("type") not in ASSET_TYPES:
                raise ValueError
            files = record.get("files")
            if not isinstance(files, dict) or "preview.png" not in files or "credits.md" not in files:
                raise ValueError
            models = MODEL_FILES.intersection(files)
            if len(models) != 1 or set(files) - PUBLISHED_FILES:
                raise ValueError
            directory = path.parent.resolve(strict=True)
            for name, info in files.items():
                file = directory / name
                if file.is_symlink() or not _inside(file, directory) or not file.is_file() or not isinstance(info, dict):
                    raise ValueError
                if file.stat().st_size != info.get("bytes") or _sha256(file) != info.get("sha256"):
                    raise ValueError
            return record
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise KeyError("Asset not found.") from None

    def public(self, record: dict) -> dict:
        result = dict(record)
        asset_id = record["id"]
        model = next(name for name in MODEL_FILES if name in record["files"])
        result.update(model_url=f"/library/{asset_id}/{model}",
                      preview_url=f"/library/{asset_id}/preview.png",
                      credits_url=f"/library/{asset_id}/credits.md")
        return result

    def list(self) -> list[dict]:
        records = []
        for directory in self.directory.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                records.append(self.public(self._load(directory.name)))
            except KeyError:
                continue
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def get(self, asset_id: str) -> dict:
        return self.public(self._load(asset_id))

    def file(self, asset_id: str, name: str) -> Path:
        record = self._load(asset_id)
        if name not in record["files"]:
            raise KeyError("Asset file not found.")
        path = self.directory / asset_id / name
        if path.is_symlink() or not _inside(path, self.directory / asset_id):
            raise KeyError("Asset file not found.")
        return path

    def copy_references(self, asset_ids: list[str], destination: Path) -> list[dict]:
        copied = []
        for asset_id in asset_ids:
            record = self._load(asset_id)
            target = destination / asset_id
            target.mkdir(parents=True, exist_ok=False)
            source = self.directory / asset_id
            for name in set(record["files"]) | {"asset.json"}:
                shutil.copy2(source / name, target / name, follow_symlinks=False)
            copied.append({"id": asset_id, "type": record["type"], "name": record["name"],
                           "parent_asset_id": record.get("parent_asset_id")})
        return copied

    def publish(self, job_directory: Path, request: dict) -> dict:
        job_directory = job_directory.resolve(strict=True)
        asset_id = request.get("job_id")
        outputs = (self.root / "outputs").resolve(strict=True)
        if (not isinstance(asset_id,str) or not re.fullmatch(r"[a-f0-9]{16}",asset_id)
                or job_directory.name != asset_id or not job_directory.is_relative_to(outputs)):
            raise RuntimeError("The asset job identity is invalid.")
        candidates = [job_directory / name for name in MODEL_FILES if (job_directory / name).is_file()]
        if len(candidates) != 1 or not _inside(candidates[0], job_directory) or not _valid_model(candidates[0]):
            raise RuntimeError("The asset output must contain one readable asset.blend or asset.glb file.")
        preview = job_directory / "preview.png"
        if not _inside(preview, job_directory) or not _valid_png(preview):
            raise RuntimeError("The asset preview is missing or is not a valid PNG image.")
        credits = job_directory / "credits.md"
        if credits.is_symlink() or not credits.is_file() or not 1 <= credits.stat().st_size <= 1024 * 1024:
            raise RuntimeError("The asset credits are missing or empty.")
        try:
            if not credits.read_text(encoding="utf-8-sig").strip():
                raise ValueError
            metadata = job_directory / "asset.json"
            if metadata.is_symlink() or not _inside(metadata, job_directory):
                raise ValueError
            generated = json.loads(metadata.read_text(encoding="utf-8-sig"))
            if not isinstance(generated, dict):
                raise ValueError
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            raise RuntimeError("The generated asset metadata is missing or invalid.") from None
        if generated.get("type", request["asset_type"]) != request["asset_type"]:
            raise RuntimeError("The generated asset type does not match the request.")

        final = self.directory / asset_id
        if final.exists():
            try:
                existing = self._load(asset_id)
                if existing.get("job_id") == asset_id:
                    return self.public(existing)
            except KeyError:
                pass
            raise RuntimeError("This asset version already exists but failed validation.")
        temporary = self.directory / f".{asset_id}.{secrets.token_hex(4)}.tmp"
        temporary.mkdir()
        try:
            published = [candidates[0], preview, credits]
            for source in published:
                shutil.copy2(source, temporary / source.name, follow_symlinks=False)
            files = {file.name: {"bytes": file.stat().st_size, "sha256": _sha256(file)}
                     for file in (temporary / item.name for item in published)}
            record = {"id": asset_id, "kind": "asset", "type": request["asset_type"],
                      "name": request["name"], "description": str(generated.get("description") or request.get("prompt", ""))[:2000],
                      "style": request.get("style"), "parent_asset_id": request.get("base_asset_id"),
                      "job_id": asset_id, "created_at": now(), "files": files}
            atomic_json(temporary / "asset.json", record)
            temporary.replace(final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self.public(record)

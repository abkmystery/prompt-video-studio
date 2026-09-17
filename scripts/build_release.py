"""Build a deterministic, allowlisted Windows source ZIP. No credentials or jobs."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re
import zipfile

REQUIRED = (
    "server.py", "requirements.txt", "README.md", "LICENSE", "PRODUCTION.md", "ASSET_PRODUCTION.md", "PRODUCTION_SCOPE.md", "TOOLS.md",
    "Setup.cmd", "Start Studio.cmd", "Stop Studio.cmd", "setup.ps1", "launch.ps1", "stop.ps1",
    "scripts/windows_common.ps1", "scripts/build_release.py",
    "studio/__init__.py", "studio/blender_scene.py", "studio/codex_bridge.py", "studio/common.py",
    "studio/jobs.py", "studio/imports.py", "studio/library.py", "studio/local_render.py", "studio/production_tools.py",
    "web/index.html", "web/app.js", "web/app-v2.js", "web/styles.css", "web/features.css",
)
OPTIONAL = (
    "NOTICE", "CHANGELOG.md", "docs/release-notes.md",
    "docs/getting-started.md", "docs/quickstart.md", "docs/troubleshooting.md", "docs/example-prompts.md",
    "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SHARING.md",
    ".gitignore", ".gitattributes", ".github/workflows/ci.yml", ".github/workflows/release.yml",
    "docs/images/studio-preview.png", "docs/media/demo.mp4",
)
BINARY_SUFFIXES = {".png", ".mp4"}

def release_files(root: Path) -> list[Path]:
    root = root.resolve()
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise ValueError("Required release files are missing: " + ", ".join(missing))
    names = set(REQUIRED)
    names.update(name for name in OPTIONAL if (root / name).is_file())
    names.update(path.relative_to(root).as_posix() for path in (root / "tests").glob("test_*.py") if path.is_file())
    result = []
    for name in sorted(names):
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
            raise ValueError("Symlinks cannot be included in a release: " + name)
        if not path.resolve().is_relative_to(root):
            raise ValueError("Release file escapes the source folder: " + name)
        result.append(path)
    return result

def build_release(root: Path, output_dir: Path, version: str) -> tuple[Path, str]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError("Version must be a semantic version such as 0.1.0 or 0.1.0-beta.1")
    root = root.resolve()
    paths = release_files(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"prompt-video-studio-{version}"
    archive = output_dir / f"{prefix}-windows.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in paths:
            name = path.relative_to(root).as_posix()
            contents = path.read_bytes()
            if path.suffix.lower() not in BINARY_SUFFIXES:
                contents = contents.replace(b"\r\n", b"\n")
                if path.suffix.lower() == ".cmd":
                    contents = contents.replace(b"\n", b"\r\n")
            info = zipfile.ZipInfo(f"{prefix}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii", newline="\n")
    return archive, checksum

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    archive, checksum = build_release(args.source, args.output_dir, args.version)
    print(archive.resolve())
    print("SHA256 " + checksum)

if __name__ == "__main__":
    main()

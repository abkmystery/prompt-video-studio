# Free local production tools

The release does not bundle media software, models, or voices. Codex can assist with obtaining missing free dependencies through its available tools and normal approvals.

## Discover installed tools

Run from the extracted app folder:

```powershell
.\.venv\Scripts\python.exe studio/production_tools.py discover
.\.venv\Scripts\python.exe studio/production_tools.py voices
```

Discovery reports actual paths without downloading anything. Voice listing requires configured Kokoro assets.

| Tool | Purpose | Official source |
| --- | --- | --- |
| Blender | 3D scenes, animation, rendering | [Download](https://www.blender.org/download/) |
| FFmpeg and ffprobe | Encoding, assembly, media checks | [Download](https://ffmpeg.org/download.html) |
| Codex | Agent planning and tool operation | [Setup](https://developers.openai.com/codex/cli/) |
| Optional Kokoro ONNX | Local stock synthetic speech | [Project](https://github.com/thewh1teagle/kokoro-onnx) |

Discovery checks environment overrides, PATH, the app's tools folder, and selected common Windows locations. To select explicit installations, use the real executable paths:

```powershell
$env:BLENDER_PATH = "C:\Tools\Blender\blender.exe"
$env:FFMPEG_PATH = "C:\Tools\FFmpeg\bin\ffmpeg.exe"
$env:PROMPT_VIDEO_CODEX = "C:\Tools\Codex\codex.exe"
& '.\Start Studio.cmd'
```

These example paths must be replaced with yours. Launch from the same PowerShell session to pass the variables to the app.

## Optional local speech

The Kokoro helper expects compatible model, voice, and Python dependency files. Follow the upstream project for versions and installation details. Its speech worker uses a compatible Python 3.12 runtime, which can differ from the core app's Python.

A standard layout is:

```text
tools/kokoro/
  models/kokoro-v1.0.onnx
  models/voices-v1.0.bin
  packages/   # kokoro-onnx, ONNX Runtime, NumPy, dependencies
  licenses/   # upstream notices
```

Overrides: `STUDIO_AUDIO_ROOT`, `KOKORO_MODEL_PATH`, `KOKORO_VOICES_PATH`, `KOKORO_PACKAGES_PATH`, and `KOKORO_PYTHON`. The root override contains models, packages, and optional licenses directories. The others identify individual files or directories. Run discovery to confirm actual availability.

The helper reuses files in place and does not download models. The fixed Windows demo can use an installed stock voice. Custom narration should report a missing dependency instead of silently delivering a narration-enabled film without speech.

## Narration and ambience

Create a UTF-8 narration text file per shot or speaker:

```powershell
.\.venv\Scripts\python.exe studio/production_tools.py synthesize --text-file "outputs/my-film/audio/scene_001.txt" --output "outputs/my-film/audio/scene_001.wav" --voice af_heart --speed 1.0 --lang en-us
.\.venv\Scripts\python.exe studio/production_tools.py ambient --duration 18 --output "outputs/my-film/audio/scene_001_ambience.wav" --seed 7
```

Speech outputs 24 kHz mono PCM WAV, sentence-timed SRT, and timing/peak/clipping metadata. List installed stock voices first. This helper does not clone voices. Keep each character's voice, speed, language, and pronunciation conventions stable.

Measure speech before timing scenes. Listen for pronunciation and performance. Sentence captions are not automatic phoneme-level lip sync. Ambience is synthesized wind and birdlike chirps, not background music or external recordings. Keep stems separate for mixing.

## Encode, assemble, verify

```powershell
.\.venv\Scripts\python.exe studio/production_tools.py encode --frames "outputs/my-film/shots/001/frames/frame_%05d.png" --input-fps 12 --fps 24 --audio "outputs/my-film/shots/001/mix.wav" --duration 18 --output "outputs/my-film/shots/001/video.mp4"
.\.venv\Scripts\python.exe studio/production_tools.py verify --video "outputs/my-film/shots/001/video.mp4"
```

Input FPS is the rendered image cadence; output FPS is the export cadence. Encoding uses H.264 and, with audio supplied, AAC stereo at 48 kHz. An explicit duration can trim speech, so measure first. Captions are not burned automatically; retain SRTs and add reviewed overlays when needed.

List ordered shot paths as a JSON array in `clips.json`. Relative paths resolve from that file. Clips need matching resolution, codecs, frame rate, and audio layout.

```powershell
.\.venv\Scripts\python.exe studio/production_tools.py concat --clips-list "outputs/my-film/clips.json" --output "outputs/my-film/video.mp4"
.\.venv\Scripts\python.exe studio/production_tools.py verify --video "outputs/my-film/video.mp4"
```

Verification probes the media when ffprobe is available and fully decodes it with FFmpeg. Also watch and listen. Existing outputs are preserved unless `--overwrite` is passed.

## Continuity and the demo

For longer films, keep one authoritative cast library and generator, stable character IDs, wardrobe, rig names, and voice mappings. Compare representative frames before full rendering and save short scene checkpoints.

The offline demo uses a small fixed template with a few sets and actions. It does not limit custom scripts authored by Codex or prove general prompt handling. Demo metadata records any ambient-only speech fallback.

## Licenses

Application MIT terms cover its own code. Tools, models, voices, and assets keep their upstream terms:

- [Blender license information](https://www.blender.org/about/license/)
- [FFmpeg legal information](https://ffmpeg.org/legal.html)
- [Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M)
- [kokoro-onnx source and license](https://github.com/thewh1teagle/kokoro-onnx)

Preserve notices and record each source in the film's credits. The app license does not grant rights to unrelated music, characters, footage, or model weights.

"""Bounded, declarative Blender film rendering. No model-written code is executed."""
from __future__ import annotations
import glob
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import wave
from array import array

_CHARACTER = {"type": "object", "additionalProperties": False, "properties": {
    "name": {"type": "string", "minLength": 1, "maxLength": 32},
    "outfit": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
    "skin": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}},
    "required": ["name", "outfit", "skin"]}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "title": {"type": "string", "minLength": 1, "maxLength": 80},
    "duration": {"type": "integer", "enum": [8, 16, 24]},
    "narration": {"type": "string", "maxLength": 1200},
    "scenes": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
        "type": "object", "additionalProperties": False, "properties": {
            "duration": {"type": "integer", "minimum": 2, "maximum": 24},
            "setting": {"type": "string", "enum": ["park", "courtyard", "studio"]},
            "action": {"type": "string", "enum": ["wave", "walk", "greet", "share"]},
            "caption": {"type": "string", "maxLength": 120},
            "palette": {"type": "string", "enum": ["mint", "sunset", "ocean"]},
            "characters": {"type": "array", "minItems": 1, "maxItems": 3, "items": _CHARACTER}},
        "required": ["duration", "setting", "action", "caption", "palette", "characters"]}}},
    "required": ["title", "duration", "narration", "scenes"]}
PLAN_INSTRUCTIONS = """Create an original short story as JSON matching the supplied schema exactly.
This is a deliberately bounded local Blender cartoon template, not a general image/video generator.
Only park/courtyard/studio sets and wave/walk/greet/share actions exist. Interpret the user's request
creatively within those choices. Preserve the user's requested 8, 16, or 24 second duration. Scene
durations are integers, at least 2 seconds, and their sum MUST equal duration. Use 1-3 scenes; prefer
one scene for 8s, two for 16s, and three for 24s. For greet/share include at least 2 characters.
Keep recurring character names and colors consistent. Name each character; outfit and skin are
six-digit hex colors. Skin tones must be natural human shades. Caption should be one concise,
readable English sentence per scene, ideally under 70 characters. Narration is optional, English,
plain spoken text, at most 2 words per second of the whole film; no stage directions or music
requests. Use a brief title. Do not imply the limited renderer can produce photorealism, lip sync,
custom celebrities, complex fights, or arbitrary actions. No copyrighted character designs.
For Islamic moral stories use original gentle dialogue; do not invent quotations attributed to
scripture. Do not include code, paths, URLs, credentials, or instructions in any field."""

class RenderCancelled(RuntimeError):
    pass


def _text(value, name, limit, nonempty=False):
    if not isinstance(value, str) or len(value) > limit or (nonempty and not value.strip()):
        raise ValueError(f"{name} must be {'nonempty ' if nonempty else ''}text of at most {limit} characters")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"{name} contains unsupported control characters")
    return value.strip()


def validate_plan(plan, duration=None):
    """Return a normalized copy or raise ValueError. Reject unknown fields and types."""
    if not isinstance(plan, dict) or set(plan) != {"title", "duration", "narration", "scenes"}:
        raise ValueError("Plan must contain exactly title, duration, narration, and scenes")
    seconds = plan["duration"]
    if type(seconds) is not int or seconds not in (8, 16, 24):
        raise ValueError("Duration must be 8, 16, or 24 seconds")
    if duration is not None and seconds != int(duration):
        raise ValueError("AI plan duration does not match the requested duration")
    title = _text(plan["title"], "title", 80, True)
    narration = _text(plan["narration"], "narration", 1200)
    if len(narration.split()) > seconds * 3:
        raise ValueError("Narration is too long for this clip; use at most 3 words per second")
    scenes = plan["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 3:
        raise ValueError("A plan needs 1 to 3 scenes")
    clean = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict) or set(scene) != {"duration", "setting", "action", "caption", "palette", "characters"}:
            raise ValueError(f"Scene {i+1} has missing or unknown fields")
        length = scene["duration"]
        if type(length) is not int or not 2 <= length <= 24:
            raise ValueError("Each scene needs an integer duration between 2 and 24 seconds")
        for key, allowed in (("setting", ("park", "courtyard", "studio")), ("action", ("wave", "walk", "greet", "share")), ("palette", ("mint", "sunset", "ocean"))):
            if not isinstance(scene[key], str) or scene[key] not in allowed:
                raise ValueError(f"Unsupported scene {key}")
        characters = scene["characters"]
        if not isinstance(characters, list) or not 1 <= len(characters) <= 3:
            raise ValueError("Each scene needs 1 to 3 characters")
        if scene["action"] in ("share", "greet") and len(characters) < 2:
            raise ValueError("Sharing and greeting scenes need at least two characters")
        people = []
        for person in characters:
            if not isinstance(person, dict) or set(person) != {"name", "outfit", "skin"}:
                raise ValueError("Character must contain exactly name, outfit, and skin")
            item = {"name": _text(person["name"], "character name", 32, True)}
            for field in ("outfit", "skin"):
                if not isinstance(person[field], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", person[field]):
                    raise ValueError(f"Character {field} must be a six-digit hex color")
                item[field] = person[field].upper()
            people.append(item)
        clean.append({"duration": length, "setting": scene["setting"], "action": scene["action"],
                      "caption": _text(scene["caption"], "caption", 120), "palette": scene["palette"], "characters": people})
    if sum(scene["duration"] for scene in clean) != seconds:
        raise ValueError("Scene durations must add up to the film duration")
    return {"title": title, "duration": seconds, "narration": narration, "scenes": clean}


def demo_plan(duration=8):
    if duration not in (8, 16, 24):
        raise ValueError("Duration must be 8, 16, or 24")
    people = [{"name": "Amal", "outfit": "#237E83", "skin": "#C98A63"},
              {"name": "Sami", "outfit": "#C76C4A", "skin": "#A66A46"}]
    scenes = [
        {"duration": 8, "setting": "courtyard", "action": "greet", "caption": "A little kindness can brighten someone's day.", "palette": "sunset", "characters": people},
        {"duration": 8, "setting": "park", "action": "walk", "caption": "Good company makes the journey sweeter.", "palette": "mint", "characters": people},
        {"duration": 8, "setting": "courtyard", "action": "share", "caption": "There is always room to share.", "palette": "sunset", "characters": people},
    ][:duration // 8]
    return {"title": "A Little Kindness", "duration": duration,
            "narration": "A little kindness can brighten someone's day." if duration == 8 else "A little kindness can brighten someone's day. Together, we can make room to share.",
            "scenes": scenes}


def discover_tools():
    user = Path.home()
    app_tools = Path(__file__).resolve().parents[1] / "tools"
    blender_candidates = [os.environ.get("BLENDER_PATH"), shutil.which("blender")]
    blender_candidates += sorted(str(p) for p in app_tools.glob("**/blender.exe"))
    # Support official portable ZIP extractions, including a user-chosen Blender folder.
    for pattern in ("blender*/blender.exe", "Blender*/blender*/blender.exe"):
        blender_candidates += sorted(glob.glob(str(user / "Downloads" / pattern)), reverse=True)
    blender_candidates += sorted(glob.glob(str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Blender Foundation" / "Blender *" / "blender.exe")), reverse=True)
    ffmpeg_candidates = [os.environ.get("FFMPEG_PATH"), shutil.which("ffmpeg")]
    ffmpeg_candidates += sorted(str(p) for p in app_tools.glob("**/ffmpeg.exe"))
    local_roots = [Path(os.environ.get("LOCALAPPDATA", str(user / "AppData" / "Local"))),
                   user / "AppData" / "Local"]
    # Codex's Windows sandbox can redirect LOCALAPPDATA; also inspect the user's installation.
    for local in dict.fromkeys(local_roots):
        ffmpeg_candidates += sorted(glob.glob(str(local / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg*" / "ffmpeg*" / "bin" / "ffmpeg.exe")), reverse=True)
    def first(paths):
        return next((str(Path(p).resolve()) for p in paths if p and Path(p).is_file()), None)
    blender, ffmpeg = first(blender_candidates), first(ffmpeg_candidates)
    return {"blender": blender, "ffmpeg": ffmpeg,
            "available": bool(blender and ffmpeg),
            "audio": "Windows built-in narration when available, otherwise synthesized ambient sound"}


def _check_cancel(event):
    if event.is_set():
        raise RenderCancelled("Rendering cancelled; partial project files were preserved")


def _run(args, cwd, log_name, cancel_event, timeout=1800, on_tick=None, env=None):
    _check_cancel(cancel_event)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_path = cwd / log_name
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen([str(v) for v in args], cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
                                   env=env, creationflags=flags)
        start = time.monotonic()
        try:
            while process.poll() is None:
                _check_cancel(cancel_event)
                if time.monotonic() - start > timeout:
                    raise RuntimeError(f"Process timed out; see {log_name}")
                if on_tick:
                    on_tick()
                time.sleep(0.2)
        except BaseException:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=flags)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise
        if process.returncode:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-1800:]
            raise RuntimeError(f"{Path(args[0]).name} failed ({process.returncode}). {tail}")


def _ambient(path, duration):
    rate = 24000
    samples = array("h")
    for i in range(rate * duration):
        t = i / rate
        # Original soft wind and sparse birdlike chirps; no recordings and no music.
        breeze = (math.sin(t * 157.9) + .45 * math.sin(t * 253.7)) * .0015
        chirp_t = t % 3.8
        chirp = .009 * math.sin(2 * math.pi * (1500 * chirp_t + 900 * chirp_t * chirp_t)) * math.sin(math.pi * chirp_t / .16) ** 2 if chirp_t < .16 else 0
        fade = min(1, t / .4, (duration - t) / .5)
        samples.append(int(32767 * (breeze + chirp) * max(0, fade)))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples.tobytes())


def _audio(plan, directory, ffmpeg, event, progress):
    _ambient(directory / "ambient.wav", plan["duration"])
    speech_ok = False
    note = "Original synthesized ambient sound; no music."
    if plan["narration"] and os.name == "nt":
        progress(3, "Creating narration with a built-in Windows voice")
        (directory / "narration.txt").write_text(plan["narration"], encoding="utf-8")
        script = """$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.Rate=0
$speaker.SetOutputToWaveFile($env:STUDIO_NARRATION_OUT)
$speaker.Speak([System.IO.File]::ReadAllText($env:STUDIO_NARRATION_TEXT))
$speaker.Dispose()
"""
        (directory / "narrate.ps1").write_text(script, encoding="utf-8")
        environment = os.environ.copy()
        environment["STUDIO_NARRATION_TEXT"] = str(directory / "narration.txt")
        environment["STUDIO_NARRATION_OUT"] = str(directory / "narration.wav")
        powershell = str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        try:
            _run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", directory / "narrate.ps1"], directory, "narration.log", event, timeout=90, env=environment)
            with wave.open(str(directory / "narration.wav"), "rb") as voice:
                voice_length = voice.getnframes() / voice.getframerate()
            speech_ok = voice_length > .1
        except RenderCancelled:
            raise
        except (RuntimeError, OSError, wave.Error):
            note += " Windows narration was unavailable; captions carry the story."
    if speech_ok:
        speed = max(1, voice_length / max(1, plan["duration"] - .8))
        stages = []
        while speed > 2:
            stages.append("atempo=2"); speed /= 2
        stages.append(f"atempo={speed:.5f}")
        chain = ",".join(stages)
        filters = f"[0:a]{chain},adelay=300:all=1,apad[v];[v][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]"
        args = [ffmpeg, "-y", "-i", "narration.wav", "-i", "ambient.wav", "-filter_complex", filters, "-map", "[a]", "-t", str(plan["duration"]), "-ar", "48000", "-ac", "2", "soundtrack.wav"]
        note = "Synthetic narration: built-in Windows stock voice. Original synthesized ambient sound; no music."
    else:
        args = [ffmpeg, "-y", "-i", "ambient.wav", "-ar", "48000", "-ac", "2", "soundtrack.wav"]
    _run(args, directory, "audio.log", event, timeout=90)
    (directory / "audio-info.json").write_text(json.dumps({"narration_available": speech_ok, "description": note}, indent=2), encoding="utf-8")


def _caption_files(plan, directory, width, height):
    def ass_time(seconds):
        return f"{seconds//3600}:{seconds//60%60:02d}:{seconds%60:02d}.00"
    def clean(text):
        return text.replace("\\", " ").replace("{", "(").replace("}", ")").replace("\r", " ").replace("\n", " ")
    fontsize = round(width * (.046 if height > width else .035))
    margin = round(height * .11)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{fontsize},&H00FFFFFF,&H00FFFFFF,&H002D2017,&H882D2017,0,0,0,0,100,100,0,0,3,9,0,2,40,40,{margin},1
Style: Title,Arial,{round(fontsize*1.5)},&H00352D1C,&H00FFFFFF,&H00FAF3E5,&H00FAF3E5,-1,0,0,0,100,100,0,0,1,2,0,8,35,35,{round(height*.08)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 1,0:00:00.00,0:00:02.00,Title,,0,0,0,,{clean(plan['title'])}"]
    srt = []
    start = 0
    for number, shot in enumerate(plan["scenes"], 1):
        end = start + shot["duration"]
        if shot["caption"]:
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{clean(shot['caption'])}")
            srt.extend([str(number), f"00:00:{start:02d},000 --> 00:00:{end:02d},000", shot["caption"], ""])
        start = end
    (directory / "captions.ass").write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    (directory / "captions.srt").write_text("\n".join(srt), encoding="utf-8")


def render(plan, output_dir: Path, aspect: str, progress, cancel_event: threading.Event) -> Path:
    """Render a validated plan with fixed Blender code. All output stays in output_dir."""
    plan = validate_plan(plan)
    tools = discover_tools()
    if not tools["available"]:
        raise RuntimeError("Local rendering needs Blender and FFmpeg. Install these free tools or set BLENDER_PATH and FFMPEG_PATH.")
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "video.mp4").exists() or (directory / "project.blend").exists():
        raise ValueError("Choose a new output directory; existing film files will not be overwritten")
    mapping = {"portrait": "9:16", "landscape": "16:9", "square": "1:1", "9:16": "9:16", "16:9": "16:9", "1:1": "1:1"}
    if aspect not in mapping:
        raise ValueError("Aspect must be portrait, landscape, square, 9:16, 16:9, or 1:1")
    aspect = mapping[aspect]
    try:
        short = int(os.environ.get("STUDIO_RENDER_SHORT_SIDE", "540"))
    except ValueError:
        short = 540
    short = max(240, min(720, short)) // 2 * 2
    long = round(short * 16 / 9 / 2) * 2
    width, height = (short, long) if aspect == "9:16" else ((long, short) if aspect == "16:9" else (short, short))
    (directory / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / "render-config.json").write_text(json.dumps({"width": width, "height": height, "pose_fps": 6}), encoding="utf-8")
    _audio(plan, directory, tools["ffmpeg"], cancel_event, progress)
    _caption_files(plan, directory, width, height)
    progress(6, "Building the editable Blender scene")
    last_frame = [-1]
    def update():
        path = directory / "render-progress.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state["frame"] != last_frame[0]:
                last_frame[0] = state["frame"]
                progress(8 + int(80 * state["frame"] / state["total"]), f"Rendering frame {state['frame']} of {state['total']}")
        except (OSError, ValueError, KeyError):
            pass
    _run([tools["blender"], "--background", "--factory-startup", "--python", Path(__file__).with_name("blender_scene.py"), "--", directory], directory, "blender.log", cancel_event, on_tick=update)
    expected = plan["duration"] * 6
    if len(list((directory / "frames").glob("frame_*.png"))) != expected:
        raise RuntimeError("Blender did not produce all expected frames; inspect blender.log")
    progress(91, "Combining animation, captions, and sound")
    _run([tools["ffmpeg"], "-y", "-framerate", "6", "-i", "frames/frame_%05d.png", "-i", "soundtrack.wav", "-vf", "ass=captions.ass", "-r", "24", "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-t", str(plan["duration"]), "-movflags", "+faststart", "video.mp4"], directory, "encode.log", cancel_event, timeout=180)
    progress(96, "Creating the thumbnail and checking the finished video")
    _run([tools["ffmpeg"], "-y", "-ss", "2.1", "-i", "video.mp4", "-frames:v", "1", "-update", "1", "thumbnail.png"], directory, "thumbnail.log", cancel_event, timeout=60)
    _run([tools["ffmpeg"], "-v", "error", "-i", "video.mp4", "-f", "null", "-"], directory, "verification.log", cancel_event, timeout=90)
    (directory / "render-info.json").write_text(json.dumps({"renderer": "Blender procedural cartoon template", "duration": plan["duration"], "width": width, "height": height, "fps": 24, "animated_poses_per_second": 6, "video": "H.264", "audio": "AAC stereo 48 kHz", "full_decode_verified": True, "limitations": "Limited template animation; no lip sync or photorealistic synthesis."}, indent=2), encoding="utf-8")
    progress(100, "Video ready")
    return directory / "video.mp4"

# Prompt Video Studio

**Create editable animated stories with Codex, Blender, and free local audio tools.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](CHANGELOG.md)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D4.svg)](docs/quickstart.md)

[**Download the latest release**](https://github.com/abkmystery/prompt-video-studio/releases/tag/v0.1.0) · [Quick start](docs/quickstart.md) · [Watch the demo](docs/media/demo.mp4) · [Example prompts](docs/example-prompts.md) · [Report a bug](https://github.com/abkmystery/prompt-video-studio/issues/new/choose)

![Prompt Video Studio: prompt editor, production settings, progress, and local video library](docs/images/studio-preview.png)

Describe your film, sign in through Codex, and follow the production in your browser. Codex plans the story, builds reusable characters, writes animation scripts, directs local rendering, and assembles narration, sound, and captions. Export an MP4 and editable production files.

**Early, Windows-first alpha.** Start with a 30-second draft. Duration choices from 30 seconds to 20 minutes are production targets; completion time, visual quality, and continuity depend on your prompt, machine, tools, and Codex usage. A completed 20-minute film is not a release guarantee.

## Features

- **Prompt to production:** style, duration, portrait/landscape, quality, narration, captions, and music preferences.
- **Official Codex sign-in:** use supported ChatGPT/Codex access without a separate video-generation API key.
- **Local animation and audio:** Blender scenes, FFmpeg assembly, and optional stock synthetic narration.
- **Reusable cast:** a character and voice bible, shared Blender assets, and a scene manifest support continuity.
- **Reusable asset library:** create characters, objects, and scenes, then derive new immutable versions without overwriting their originals.
- **Continue or revise:** extend a completed film, or import a video or script and create a separate revised job with copied source material.
- **Approval choice:** review sensitive actions yourself or select automatic Codex safety review for a job; both modes keep the same local workspace limits.
- **Saved progress:** continue the same Codex thread and production checkpoints after an interruption.
- **Visible work:** agent updates, action approvals, video previews, and editable project downloads.
- **Fixed local demo:** test the rendering pipeline without starting a Codex production turn.

## Start on Windows

1. Download the Windows ZIP from [Releases](https://github.com/abkmystery/prompt-video-studio/releases/tag/v0.1.0).
2. Extract the entire ZIP into a regular folder in **Downloads**. Do not launch from inside the ZIP.
3. Double-click **Start Studio.cmd**. First launch checks Python and prepares a local environment. If Python is missing, setup offers installation through Windows Package Manager or an official manual installer link.
4. Connect using **Sign in with ChatGPT** and complete the official browser sign-in.
5. Enter a short prompt and select **Create with Codex**. Review action approvals when they appear.

The app opens at [127.0.0.1:8765](http://127.0.0.1:8765/). **Stop Studio.cmd** closes the background server.

Python 3.11+ and a working [official Codex installation](https://developers.openai.com/codex/cli/) are prerequisites. Blender, FFmpeg, and optional speech models are separate downloads. Codex can help obtain missing free tools from official sources through its available tools and normal approval flow.

[Quick start](docs/quickstart.md) · [Tool setup](TOOLS.md) · [Troubleshooting](docs/troubleshooting.md)

## Try this prompt

> Create a 30-second stylized 3D story about two neighbors sharing shade on a warm day. Use two original adult characters with consistent clothing and voices, a sunny courtyard, three clear camera shots, gentle English narration, and readable captions. End with “A little kindness goes a long way.” Use ambient sound and no music.

Keep your first film simple: two characters, one setting, and one clear action. [More prompts →](docs/example-prompts.md)

## How it works

```text
Prompt → Codex script, cast, voices, and scene plan
       → Local Blender animation + speech and sound
       → Scene checkpoints → FFmpeg assembly → export checks
       → MP4 + captions + editable project + credits
```

The studio communicates with the official Codex app server. Codex directs the tools available in your installation; browser and computer-use capabilities depend on what that environment exposes.

The [production contract](PRODUCTION.md) calls for a script, story bible, reusable assets, audio stems, scene checkpoints, and credits. Technical export checks examine duration, aspect ratio, codecs, requested audio/captions, required files, and full video decoding. Storytelling, factual accuracy, pronunciation, and visual continuity still need human review.

## Free tools, account usage, and privacy

The studio source is free under MIT. Local media tools avoid a separate paid video or voice API. **Codex access is separate:** a supported account with available usage is required, and account terms and limits still apply. Long films can take hours and multiple sessions.

The interface and rendered files are local, but **Codex is a cloud AI service**. Prompts and content shared with Codex are processed under your OpenAI account settings and terms. This is not an entirely offline AI generator. The fixed demo can run without a Codex turn once its dependencies are installed.

Keep the extracted folder in Downloads to keep production files outside OneDrive. Jobs live in `outputs/<project-id>/`; shared tools and assets belong in `tools/` and `assets/`. Codex maintains its own normal account and session data separately.

Published reusable assets live in `library/<asset-id>/`, and imported source material lives in `imports/<import-id>/`. Library versions are immutable: editing from a base creates a new ID and preserves the original. Continuations also create new jobs and copy only validated source files into their own job directory.

The app does not upload films to YouTube. Review and publish them yourself. Logs and projects can contain prompts, source material, and local paths: review before sharing. Never paste credentials or authentication tokens into a prompt.

## Current limits

- Alpha quality; productions can fail or need correction.
- Windows-first and single-user; other platforms are not release-tested.
- Targets up to 20 minutes are not a quality or rendering-time guarantee.
- Resume requires the server to be running; this is not an unattended scheduler.
- The fixed demo illustrates a small template, not every prompted production.
- Local speech and computer-use capabilities depend on installed tools.
- Keep the loopback server private. It is not a hosted multi-user service.

## Run from source

Clone or download this repository into a local folder:

```powershell
.\Setup.cmd
& '.\Start Studio.cmd'
```

For development:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe server.py --port 8765
```

Stop an existing studio before launching another server on the same port. The core application uses Python's standard library; production tools have separate installations.

## Contribute and share

Reproducible bugs, setup fixes, clearer prompts, and animation/audio improvements are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [changelog](CHANGELOG.md).

If the project helps you, a GitHub star makes it easier for others to find. Share examples with tool and asset credits.

## License

Application code is [MIT licensed](LICENSE). Third-party tools, model weights, voices, and source assets retain their own licenses; MIT does not replace them. See [tool licenses](TOOLS.md#licenses).

An independent community project, not affiliated with or endorsed by OpenAI, the Blender Foundation, or FFmpeg.

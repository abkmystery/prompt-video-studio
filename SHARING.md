# Sharing and moving the studio

Share the clean [public release](https://github.com/abkmystery/prompt-video-studio/releases/tag/v0.1.0), not a ZIP of your working studio folder. Working folders may contain private prompts, source assets, logs, session references, and generated media.

## Another computer

Extract the release into Downloads and run **Start Studio.cmd**. Complete prerequisite setup and sign in through the official Codex flow. Each computer needs appropriate Codex access; never transfer authentication files.

The release does not bundle Python, Codex, Blender, FFmpeg, or speech models. See [TOOLS.md](TOOLS.md).

## Moving a film

Back up the whole `outputs/<project-id>/` folder and any referenced shared assets. A finished MP4 is portable. An unfinished job can depend on original tool paths and access to the saved Codex thread; moving files alone does not transfer that access.

Keep the original until the copied media and project have been checked. Review source credits, third-party terms, and metadata before publication. The studio does not publish automatically.

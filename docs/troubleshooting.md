# Troubleshooting

| Problem | What to check |
| --- | --- |
| Studio will not open | Run Setup.cmd, check Python 3.11+, then Start Studio.cmd. Read server.log and server-error.log. |
| Port 8765 is occupied | Stop the other studio using its own Stop Studio.cmd. Launchers refuse to take over another extracted folder's server. |
| Codex is missing | Follow the official Codex installation instructions, then restart Studio. Use PROMPT_VIDEO_CODEX for an undiscovered executable. |
| Sign-in fails | Complete the official browser flow. Do not copy authentication files or paste credentials into prompts. |
| Production hits usage limits | Preserve files and resume when the account has available Codex usage. |
| Render appears slow | Check the current stage and files. Percentages are approximate milestones, not a frame meter. Start with a small draft. |
| Missing media software | Run the discovery command below. Configure BLENDER_PATH or FFMPEG_PATH if needed. |
| Narration unavailable | Configure local speech assets and a compatible runtime. See TOOLS.md. |
| Export verification fails | Read saved verification notes, keep intermediate files, and resume to repair the relevant stage. |

Run these from the extracted app folder:

```powershell
.\.venv\Scripts\python.exe studio/production_tools.py discover
```

For terminal diagnostics, stop the background server first:

```powershell
.\.venv\Scripts\python.exe server.py --port 8765
```

Resume depends on saved files and access to the original Codex thread. It cannot guarantee recovery from a deleted thread, corrupted output, or unavailable dependency.

The [tool guide](../TOOLS.md) covers rendering and speech configuration. Technical verification does not replace watching and listening to a film.

For a [bug report](https://github.com/abkmystery/prompt-video-studio/issues/new/choose), include the version, environment, production stage, and a small reproducible prompt. Remove credentials, account details, private prompts, personal paths, and source material from logs and screenshots. Follow [SECURITY.md](../SECURITY.md) for vulnerabilities.

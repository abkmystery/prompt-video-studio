# Contributing

Focused fixes, reproducible bug reports, setup improvements, and animation/audio examples are welcome.

## Development

Install Python 3.11+, clone the repository locally, and run `Setup.cmd`. Run tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Start with `Start Studio.cmd`, or run `server.py --port 8765` using the local environment for terminal diagnostics. Stop any existing studio on that port first.

## Changes and reports

- Describe the concrete problem and user-visible improvement.
- Keep pull requests focused; add useful regression tests for behavioral changes.
- Update documentation when setup, permissions, or outputs change.
- State what was tested and what remains unverified.
- Use small original sample prompts when reporting generation issues.
- Preserve local-only defaults, transparent approvals, and resumable files.
- Do not commit credentials, logs, runtime sessions, private films, models, or downloaded software.

Review logs and screenshots for private content before posting. Report vulnerabilities through [SECURITY.md](SECURITY.md).

Contributions are distributed under the repository's MIT license. Retain attribution and applicable licenses for third-party material.

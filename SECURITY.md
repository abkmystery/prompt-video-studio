# Security and privacy

Prompt Video Studio is an alpha for one user on their own computer, not a public hosted service.

The server binds to loopback and uses host/origin checks and a per-run token for state-changing requests. Keep it private: do not port-forward it or expose it through a public tunnel.

Codex can operate production tools and files according to its configured permissions. Review commands, paths, and scopes before approving actions. The app uses the official Codex app server and sign-in flow; never paste credentials or authentication tokens into prompts or issues.

Local rendering does not mean offline AI processing. Prompts and tool context sent to Codex are handled under the connected account's settings and terms. Optional tools can have their own data flows.

Jobs and logs can contain private scripts, media, and local paths. Codex maintains its own normal account/session files separately. Review any files before sharing.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/abkmystery/prompt-video-studio/security/advisories/new) if enabled. Include the affected version, environment, reproduction steps with non-sensitive data, and expected security boundary.

If private reporting is unavailable, ask for a private reporting channel in an issue without publishing exploit details. Do not include credentials or private projects, and only test systems you control.

The project maintains the latest public alpha. No response-time or long-term support commitment is offered.

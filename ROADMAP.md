# SwarmBox Roadmap

SwarmBox is a Python orchestration layer for coding agents that need repeatable git workspaces, sandbox startup, prompt execution, logs, sessions, and recovery paths.

## Current Release Line

- Python API for `run`, `interactive`, `create_sandbox`, `create_worktree`, and async wrappers.
- First-class adapters for Claude Code, Codex, OpenCode, and Pi.
- Registry-backed generic adapters for SwarmVault and SwarmClaw-compatible command-line agents.
- Host, Docker, Podman, Vercel, and Daytona sandbox entry points.
- Head, merge-to-head, explicit branch, and owned worktree workflows.
- Packaged init templates for blank, simple loop, sequential review, parallel planning, and parallel planning with review.
- Prompt files, prompt args, built-in branch args, and shell expansion.
- Stream callbacks, session helpers, cancellation, idle timeout, startup/copy/sync timeouts, and recovery messages.
- CLI support for init, run, agents, templates, and image lifecycle helpers.

## Hardening Before 1.0

- Broaden live smoke coverage for Docker, Podman, Vercel, Daytona, and real coding-agent CLIs.
- Add more parser fixtures as agent JSON stream formats evolve.
- Improve type coverage until `mypy src` can become a required release gate.
- Add richer examples for CI automation and multi-agent backlog workflows.
- Continue tightening cleanup behavior for interrupted cloud sandbox startup.

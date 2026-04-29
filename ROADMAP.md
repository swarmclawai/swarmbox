# SwarmBox Feature Status

SwarmBox is moving toward a complete, production-ready orchestration layer for
coding agents in local, container, and cloud workspaces.

## Implemented

- Python API: `run`, `interactive`, `create_sandbox`, `create_worktree`, plus
  async wrappers.
- Agent providers: Claude Code, Codex, OpenCode, Pi, generic command agents,
  and a SwarmVault/SwarmClaw-seeded registry.
- Sandbox providers: Docker, Podman, no-sandbox, Vercel, and Daytona entry
  points.
- Branch strategies: `head`, `merge-to-head`, and explicit named branches.
- Prompt files, inline prompts, `{{ARG}}` substitution, built-in branch args,
  and marked shell expansion.
- Host and sandbox lifecycle hooks with timeout and cancellation support.
- Worktree creation, stale pruning, dirty preservation, and commit collection.
- Stream callbacks, text buffering, idle timeout enforcement, and basic
  cancellation tokens.
- Session transfer helpers and configurable session paths.
- Sync-in/sync-out recovery artifacts and copy-pastable recovery messages.
- CLI commands for init, run, agent discovery, and image lifecycle helpers.

## Hardening Before 1.0

- Broaden live smoke coverage for Docker, Podman, Vercel, Daytona, and real
  agent CLIs.
- Expand parser fixtures as CLI JSON stream formats evolve.
- Continue improving template ergonomics for multi-agent backlog workflows.
- Add CI jobs for unit tests, linting, typing, packaging, and secret checks.

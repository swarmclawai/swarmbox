# Changelog

## 0.3.0

- Fixed owned worktree lifecycle so `Worktree.run()`, `Worktree.interactive()`, and `Worktree.create_sandbox()` keep the worktree until `worktree.close()`.
- Added sandbox startup timeout enforcement with `Timeouts.sandbox_start_ms`.
- Expanded the public API for custom sandbox providers, mounts, prompt helpers, session stores, errors, and stream metadata.
- Moved init templates into packaged template files and added `swarmbox templates list`.
- Added guided `swarmbox init --interactive` setup.
- Added clearer CLI run summaries and formatted SwarmBox errors.
- Rewrote the README with capability tables, examples, troubleshooting, and release-accurate development commands.
- Added GitHub Actions CI for tests, linting, compile checks, and package validation.

## 0.2.0

- Initial public SwarmBox release with Python orchestration APIs, agent registry, sandbox providers, prompt handling, sessions, streaming, and CLI scaffolding.

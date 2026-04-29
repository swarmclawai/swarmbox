# SwarmBox

[![PyPI version](https://img.shields.io/pypi/v/swarmbox.svg)](https://pypi.org/project/swarmbox/)
[![Python versions](https://img.shields.io/pypi/pyversions/swarmbox.svg)](https://pypi.org/project/swarmbox/)
[![Package status](https://img.shields.io/pypi/status/swarmbox.svg)](https://pypi.org/project/swarmbox/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

SwarmBox runs coding agents in isolated, git-aware workspaces from Python.
Use it to hand a task to Claude Code, Codex, OpenCode, Pi, or another
terminal-native coding agent, let the agent work in a branch or sandbox, then
collect the commits and logs in a predictable way.

It is built for local automation, CI workers, and multi-agent workflows where
you need repeatable setup, branch isolation, prompt files, streamed output,
and recovery paths when sync or merge operations need human help.

## Install

```bash
python -m pip install swarmbox
```

Optional sandbox SDKs:

```bash
python -m pip install "swarmbox[vercel]"
python -m pip install "swarmbox[daytona]"
```

For local development:

```bash
python -m pip install -e ".[dev]"
```

## Quick Start

Create a SwarmBox config directory:

```bash
swarmbox init --agent codex-cli --sandbox docker --template blank --no-build
```

Run an agent from Python:

```python
from swarmbox import codex, docker, run

result = run(
    agent=codex("gpt-5.4-codex"),
    sandbox=docker(),
    prompt_file=".swarmbox/prompt.md",
    name="fix-login",
)

print(result.branch)
print([commit.sha for commit in result.commits])
```

Run from the CLI:

```bash
swarmbox run \
  --agent codex-cli \
  --sandbox docker \
  --prompt-file .swarmbox/prompt.md \
  --name fix-login
```

## Core Concepts

- **Agents** build print-mode and interactive commands for coding-agent CLIs.
- **Sandboxes** run those commands on the host, in Docker/Podman, or in cloud
  workspaces.
- **Worktrees** isolate task branches under `.swarmbox/worktrees`.
- **Prompt files** support `{{ARG}}` substitution plus marked shell expansion
  with ``!`command` ``.
- **Hooks** run setup commands on the host or inside the sandbox before the
  agent starts.
- **Sync and recovery** move commits, diffs, and untracked files between
  isolated sandboxes and the host repo.

## Supported Agents

First-class adapters:

- `claude-cli`
- `codex-cli`
- `opencode-cli`
- `pi`

Registry-backed generic adapters include Gemini CLI, GitHub Copilot CLI,
Cursor Agent, Qwen Code, Goose, Aider, Amp, Augment, Continue, OpenHands,
Replit Agent, Roo Code, Warp, Windsurf, Zencoder, and other SwarmVault or
SwarmClaw-compatible command-line agents.

Useful commands:

```bash
swarmbox agents list
swarmbox agents detect
swarmbox agents doctor
```

## Sandbox Providers

```python
from swarmbox import docker, podman, no_sandbox, vercel, daytona
```

- `docker()` and `podman()` use bind mounts for fast local iteration.
- `no_sandbox()` runs directly in the selected worktree.
- `vercel()` and `daytona()` use optional SDK integrations for isolated cloud
  workspaces.

Image helpers:

```bash
swarmbox docker build-image
swarmbox docker remove-image
swarmbox podman build-image
swarmbox podman remove-image
```

## Branch Strategies

```python
from swarmbox import branch, head, merge_to_head
```

- `head()` runs on the current branch and bind-mounted repo.
- `merge_to_head()` runs on a temporary branch and merges successful commits
  back to the current branch.
- `branch("swarmbox/task-123", base_branch="main")` runs on an explicit branch.

## Streaming, Sessions, And Timeouts

```python
from swarmbox import Timeouts, codex, docker, run

events = []

result = run(
    agent=codex("gpt-5.4-codex"),
    sandbox=docker(),
    prompt_file=".swarmbox/prompt.md",
    idle_timeout_seconds=600,
    timeouts=Timeouts(copy_to_worktree_ms=60_000),
    logging={
        "type": "file",
        "on_agent_stream_event": events.append,
    },
)
```

SwarmBox can capture streamed text and tool-call events, enforce idle
timeouts, transfer Claude-compatible session files, and preserve worktrees when
an agent leaves uncommitted changes behind.

## Configuration

`swarmbox init` creates `.swarmbox/` with:

- `.env.example` for agent and backlog manager credentials
- `Dockerfile` or `Containerfile`
- `main.py`
- prompt templates
- `swarmbox.json`

Runtime secrets belong in `.swarmbox/.env`, which is ignored by git.
Never commit API keys, PyPI tokens, provider tokens, or agent credentials.

## Templates

- `blank`
- `simple-loop`
- `sequential-reviewer`
- `parallel-planner`
- `parallel-planner-with-review`

Each template can be paired with GitHub Issues or Beads backlog commands and
customized after scaffolding.

## Development

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
ruff check .
python -m build
twine check dist/*
```

Live smoke tests for Docker, Podman, Vercel, Daytona, and real coding-agent
CLIs should be run before a release when those tools are available.

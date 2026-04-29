# SwarmBox

[![PyPI version](https://img.shields.io/pypi/v/swarmbox.svg)](https://pypi.org/project/swarmbox/)
[![Python versions](https://img.shields.io/pypi/pyversions/swarmbox.svg)](https://pypi.org/project/swarmbox/)
[![Package status](https://img.shields.io/pypi/status/swarmbox.svg)](https://pypi.org/project/swarmbox/)
[![CI](https://github.com/swarmclawai/swarmbox/actions/workflows/ci.yml/badge.svg)](https://github.com/swarmclawai/swarmbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

SwarmBox runs coding agents in isolated, git-aware workspaces from Python. It gives Claude Code, Codex, OpenCode, Pi, and registry-backed terminal agents a repeatable place to work, a branch strategy, a prompt, streamed logs, session handling, and a cleanup path.

Use it when a task should happen outside your current checkout, inside Docker or Podman, or inside a cloud sandbox while still producing normal git commits you can inspect, merge, or discard.

## Install

```bash
python -m pip install swarmbox
```

Optional cloud sandbox integrations:

```bash
python -m pip install "swarmbox[vercel]"
python -m pip install "swarmbox[daytona]"
```

For local development:

```bash
python -m pip install -e ".[dev]"
```

## Quick Start

Create a project config:

```bash
swarmbox init --interactive
```

Run a task from the CLI:

```bash
swarmbox run \
  --agent codex-cli \
  --sandbox docker \
  --prompt-file .swarmbox/prompt.md \
  --name fix-login
```

Run the same shape from Python:

```python
from swarmbox import Timeouts, codex, docker, run

result = run(
    agent=codex("gpt-5.4-codex"),
    sandbox=docker(),
    prompt_file=".swarmbox/prompt.md",
    name="fix-login",
    idle_timeout_seconds=600,
    timeouts=Timeouts(sandbox_start_ms=60_000, copy_to_worktree_ms=60_000),
)

print(result.branch)
print([commit.sha for commit in result.commits])
```

## What SwarmBox Manages

| Area | What you get |
| --- | --- |
| Agents | First-class adapters plus generic command wrappers for SwarmVault-style CLI agents. |
| Sandboxes | Host execution, Docker, Podman, Vercel Sandbox, and Daytona entry points. |
| Git isolation | Current-branch, temporary merge branch, explicit branch, and owned worktree workflows. |
| Prompts | Inline prompts, prompt files, `{{ARG}}` substitution, built-in branch args, and shell expansion with ``!`command` ``. |
| Runtime control | Stream events, idle timeout, cancellation token, startup/copy/sync timeouts, and session transfer helpers. |
| Recovery | Dirty worktree preservation, sync recovery artifacts, log files, and human-readable error messages. |

## Supported Agents

First-class adapters:

| ID | Binary | Notes |
| --- | --- | --- |
| `claude-cli` | `claude` | Stream JSON, session capture, interactive mode. |
| `codex-cli` | `codex` | JSON print-mode execution through stdin. |
| `opencode-cli` | `opencode` | Print and interactive command support. |
| `pi` | `pi` | JSON-streaming print-mode support. |

Registry-backed generic adapters include Gemini CLI, GitHub Copilot CLI, Cursor Agent, Qwen Code, Goose, Aider, Amp, Augment, Continue, OpenHands, Replit Agent, Roo Code, Warp, Windsurf, Zencoder, and other SwarmVault or SwarmClaw-compatible command-line agents.

Useful commands:

```bash
swarmbox agents list
swarmbox agents detect
swarmbox agents doctor
```

For a custom CLI, use `command_agent`:

```python
from swarmbox import command_agent, no_sandbox, run

agent = command_agent(
    name="my-agent",
    command_template=["my-agent", "run", "--model", "{model}", "{prompt}"],
    model="default",
)

run(agent=agent, sandbox=no_sandbox(), prompt="Refactor the parser.")
```

## Sandbox Providers

| Provider | Function | Best for |
| --- | --- | --- |
| Host | `no_sandbox()` | Fast local workflows and trusted agents. |
| Docker | `docker()` | Local isolation with bind mounts. |
| Podman | `podman()` | Rootless/container-native Linux workflows. |
| Vercel | `vercel()` | Cloud sandbox execution with the optional Vercel SDK. |
| Daytona | `daytona()` | Cloud development sandbox execution with the optional Daytona SDK. |

```python
from swarmbox import MountConfig, docker

sandbox = docker(
    image_name="swarmbox:my-project",
    mounts=[MountConfig("~/shared-cache", "~/cache", readonly=False)],
    env={"NODE_ENV": "development"},
)
```

Image helpers:

```bash
swarmbox docker build-image
swarmbox docker remove-image
swarmbox podman build-image
swarmbox podman remove-image
```

## Branch And Worktree Modes

```python
from swarmbox import branch, create_worktree, head, merge_to_head
```

| Strategy | Behavior |
| --- | --- |
| `head()` | Run directly on the current branch. |
| `merge_to_head()` | Run on a temporary branch, then merge successful commits back. |
| `branch("swarmbox/task-123")` | Run on an explicit branch. |
| `create_worktree(...)` | Keep a reusable worktree alive until `worktree.close()`. |

Owned worktrees are useful when several operations should happen in the same isolated checkout:

```python
from swarmbox import branch, codex, create_worktree, no_sandbox

worktree = create_worktree(branch_strategy=branch("swarmbox/task-123"))
try:
    worktree.run(
        agent=codex("gpt-5.4-codex"),
        sandbox=no_sandbox(),
        prompt="Implement the task and commit it.",
    )
finally:
    worktree.close()
```

## Init Templates

`swarmbox init` creates `.swarmbox/` with prompt files, a container file, `.env.example`, `main.py`, and `swarmbox.json`.

```bash
swarmbox templates list
swarmbox init \
  --agent codex-cli \
  --sandbox podman \
  --template parallel-planner-with-review \
  --backlog github-issues \
  --no-build
```

Templates:

| Template | Purpose |
| --- | --- |
| `blank` | Minimal prompt scaffold. |
| `simple-loop` | Implement one backlog item and close it. |
| `sequential-reviewer` | Implementation prompt plus review prompt and coding standards. |
| `parallel-planner` | Planning, implementation, and merge prompts. |
| `parallel-planner-with-review` | Parallel planning plus review prompts. |

Backlog integrations currently scaffold commands for GitHub Issues and Beads. The generated files are normal text files, so you can replace the commands with Jira, Linear, custom scripts, or local task files.

## Configuration And Secrets

Runtime secrets belong in `.swarmbox/.env`; generated projects ignore that file by default. Keep API keys, PyPI tokens, provider tokens, and agent credentials out of git.

Typical keys:

```dotenv
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GH_TOKEN=
DAYTONA_API_KEY=
VERCEL_TOKEN=
```

SwarmBox also reads process environment values for keys declared in `.swarmbox/.env`, so CI systems can inject secrets without writing them to disk.

## Streaming, Sessions, And Timeouts

```python
from swarmbox import CancellationToken, Timeouts, claude_code, docker, run

events = []
token = CancellationToken()

result = run(
    agent=claude_code("claude-sonnet-4-6"),
    sandbox=docker(),
    prompt_file=".swarmbox/implement-prompt.md",
    idle_timeout_seconds=600,
    timeouts=Timeouts(
        sandbox_start_ms=60_000,
        copy_to_worktree_ms=60_000,
        sync_in_ms=120_000,
        sync_out_ms=120_000,
    ),
    logging={"type": "file", "on_agent_stream_event": events.append},
    signal=token,
)
```

Claude-compatible session files can be transferred between host and sandbox paths with `host_session_store`, `sandbox_session_store`, and `transfer_session`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `docker` or `podman` not found | Install the runtime or use `no_sandbox()`. |
| Container starts but agent command is missing | Rebuild the generated image after editing `.swarmbox/Dockerfile` or `.swarmbox/Containerfile`. |
| Git refuses to operate in a mounted repo | Confirm the sandbox can write its home directory and git safe-directory config. |
| Agent hangs silently | Set `idle_timeout_seconds` and check the log file under `.swarmbox/logs`. |
| Sync from an isolated provider fails | Use the recovery instructions printed in the error message and inspect `.swarmbox/patches`. |

## Development

```bash
python -m pip install -e ".[dev]"
PYTHONPATH=src python -m pytest
python -m ruff check .
python -m compileall -q src
python -m build
python -m twine check dist/*
```

Live smoke tests for Docker, Podman, Vercel, Daytona, and real coding-agent CLIs should be run before release when the required tools and credentials are available.

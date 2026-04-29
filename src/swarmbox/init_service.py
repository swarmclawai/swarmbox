import json
import subprocess
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Dict, List, Optional

from .agents import AGENT_REGISTRY
from .errors import InitError

GITIGNORE = ".env\nlogs/\nworktrees/\npatches/\n"


@dataclass(frozen=True)
class TemplateMetadata:
    name: str
    description: str


TEMPLATES = [
    TemplateMetadata("blank", "Bare scaffold: write your own prompt and orchestration"),
    TemplateMetadata("simple-loop", "Picks backlog items one by one and closes them"),
    TemplateMetadata("sequential-reviewer", "Implements items one by one, with a review step"),
    TemplateMetadata("parallel-planner", "Plans parallelizable items, executes branches, merges"),
    TemplateMetadata("parallel-planner-with-review", "Parallel planner with per-branch review"),
]


@dataclass(frozen=True)
class BacklogManager:
    name: str
    label: str
    template_args: Dict[str, str]
    env_example: str = ""


GITHUB_TOOLS = """# Install GitHub CLI
RUN apt-get update && apt-get install -y curl gnupg \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update && apt-get install -y gh \
  && rm -rf /var/lib/apt/lists/*
"""

BACKLOG_MANAGERS = {
    "github-issues": BacklogManager(
        "github-issues",
        "GitHub Issues",
        {
            "LIST_TASKS_COMMAND": "gh issue list --state open --label SwarmBox --json number,title,body",
            "VIEW_TASK_COMMAND": "gh issue view {{TASK_ID}}",
            "CLOSE_TASK_COMMAND": 'gh issue close {{TASK_ID}} --comment "Completed by SwarmBox"',
            "BACKLOG_MANAGER_TOOLS": GITHUB_TOOLS,
        },
        "GH_TOKEN=\n",
    ),
    "beads": BacklogManager(
        "beads",
        "Beads",
        {
            "LIST_TASKS_COMMAND": "bd ready --json",
            "VIEW_TASK_COMMAND": "bd show {{TASK_ID}}",
            "CLOSE_TASK_COMMAND": 'bd close {{TASK_ID}} "Completed by SwarmBox"',
            "BACKLOG_MANAGER_TOOLS": "",
        },
    ),
}


SANDBOX_PROVIDERS = {
    "docker": {"label": "Docker", "containerfile": "Dockerfile", "cli": "docker"},
    "podman": {"label": "Podman", "containerfile": "Containerfile", "cli": "podman"},
}


AGENT_INSTALLS = {
    "claude-cli": ("claude_code", "claude-sonnet-4-6", "RUN curl -fsSL https://claude.ai/install.sh | bash", "ANTHROPIC_API_KEY=\n"),
    "codex-cli": ("codex", "gpt-5.4-codex", "RUN npm install -g @openai/codex", "OPENAI_API_KEY=\n"),
    "opencode-cli": ("opencode", "claude-sonnet-4-6", "RUN npm install -g opencode-ai@latest", "OPENCODE_API_KEY=\n"),
    "pi": ("pi", "claude-sonnet-4-6", "RUN npm install -g @mariozechner/pi-coding-agent", "ANTHROPIC_API_KEY=\n"),
}


def list_templates() -> List[TemplateMetadata]:
    return list(TEMPLATES)


def _agent_install(agent_id: str):
    meta = AGENT_REGISTRY.get(agent_id)
    if not meta:
        raise InitError("Unknown agent %s" % agent_id)
    return AGENT_INSTALLS.get(
        agent_id,
        (
            "agent_from_metadata",
            meta.default_model,
            "# Install %s manually or customize this image for %s" % (meta.display_name, meta.binary_name),
            "",
        ),
    )


def _containerfile(agent_id: str, backlog: BacklogManager) -> str:
    _, _, install_line, _ = _agent_install(agent_id)
    return """FROM node:22-bookworm

RUN apt-get update && apt-get install -y git curl jq python3 python3-pip sudo \\
  && rm -rf /var/lib/apt/lists/*

%s

%s

RUN usermod -d /home/agent -m -l agent node
USER agent
ENV PATH="/home/agent/.local/bin:$PATH"
WORKDIR /home/agent
ENTRYPOINT ["sleep", "infinity"]
""" % (backlog.template_args["BACKLOG_MANAGER_TOOLS"].strip(), install_line)


def _main_py(agent_id: str, model: str, sandbox_name: str) -> str:
    factory, _, _, _ = _agent_install(agent_id)
    if factory == "agent_from_metadata":
        agent_expr = 'agent_from_metadata("%s", model="%s")' % (agent_id, model)
        import_line = "from swarmbox.agents import agent_from_metadata"
    else:
        agent_expr = '%s("%s")' % (factory, model)
        import_line = "from swarmbox import %s" % factory
    sandbox_import = "docker" if sandbox_name == "docker" else "podman"
    return """from swarmbox import run
%s
from swarmbox.sandboxes import %s


result = run(
    agent=%s,
    sandbox=%s(),
    prompt_file=".swarmbox/prompt.md",
    copy_to_worktree=["node_modules"],
    name="swarmbox",
)

print({"branch": result.branch, "commits": [c.sha for c in result.commits]})
""" % (import_line, sandbox_import, agent_expr, sandbox_import)


def _template_files(template_name: str) -> Dict[str, str]:
    template_root = files("swarmbox.templates").joinpath(template_name)
    if not template_root.is_dir():
        raise InitError("Unknown template %s" % template_name)
    output = {}
    for item in sorted(template_root.iterdir(), key=lambda entry: entry.name):
        if item.is_file():
            output[item.name] = item.read_text(encoding="utf-8")
    return output


def _substitute(content: str, args: Dict[str, str]) -> str:
    for key, value in args.items():
        content = content.replace("{{%s}}" % key, value)
    return content


def scaffold(
    repo_dir: str,
    agent_id: str = "claude-cli",
    model: Optional[str] = None,
    template_name: str = "blank",
    backlog_manager_name: str = "github-issues",
    sandbox_provider_name: str = "docker",
    create_label: bool = True,
) -> str:
    repo = Path(repo_dir)
    config = repo / ".swarmbox"
    if config.exists():
        raise InitError(".swarmbox/ directory already exists")
    backlog = BACKLOG_MANAGERS[backlog_manager_name]
    sandbox = SANDBOX_PROVIDERS[sandbox_provider_name]
    meta = AGENT_REGISTRY[agent_id]
    chosen_model = model or meta.default_model
    config.mkdir()
    (config / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    _, _, _, env_example = _agent_install(agent_id)
    (config / ".env.example").write_text(env_example + backlog.env_example, encoding="utf-8")
    (config / sandbox["containerfile"]).write_text(_containerfile(agent_id, backlog), encoding="utf-8")
    (config / "main.py").write_text(_main_py(agent_id, chosen_model, sandbox_provider_name), encoding="utf-8")
    template_args = dict(backlog.template_args)
    for name, content in _template_files(template_name).items():
        if not create_label:
            content = content.replace("--label SwarmBox", "")
        (config / name).write_text(_substitute(content, template_args), encoding="utf-8")
    metadata = {
        "agent": agent_id,
        "model": chosen_model,
        "template": template_name,
        "backlog_manager": backlog_manager_name,
        "sandbox_provider": sandbox_provider_name,
    }
    (config / "swarmbox.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return "main.py"


def maybe_create_github_label(repo_dir: str) -> None:
    subprocess.run(
        [
            "gh",
            "label",
            "create",
            "SwarmBox",
            "--description",
            "Issues for SwarmBox to work on",
            "--color",
            "F9A825",
        ],
        cwd=repo_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

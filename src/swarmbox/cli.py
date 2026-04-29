import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .agents import AGENT_REGISTRY, agent_from_metadata
from .api import run
from .display import format_error, format_run_summary
from .errors import SwarmBoxError
from .init_service import BACKLOG_MANAGERS, SANDBOX_PROVIDERS, list_templates, scaffold
from .mounts import default_image_name
from .sandbox import docker, podman


def _prompt_choice(label: str, choices, default: str) -> str:
    ordered = list(choices)
    while True:
        print("%s:" % label)
        for index, choice in enumerate(ordered, start=1):
            marker = " (default)" if choice == default else ""
            print("  %s. %s%s" % (index, choice, marker))
        raw = input("> ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(ordered):
            return ordered[int(raw) - 1]
        if raw in ordered:
            return raw
        print("Choose one of: %s" % ", ".join(ordered))


def _prompt_text(label: str, default: str) -> str:
    raw = input("%s [%s]: " % (label, default)).strip()
    return raw or default


def _prompt_bool(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input("%s [%s]: " % (label, suffix)).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "true", "1"):
            return True
        if raw in ("n", "no", "false", "0"):
            return False
        print("Enter yes or no.")


def _should_prompt_init(args) -> bool:
    if args.yes:
        return False
    if args.interactive:
        return True
    chosen = (args.agent, args.model, args.template, args.backlog, args.sandbox, args.build, args.create_label)
    return sys.stdin.isatty() and all(value is None for value in chosen)


def _resolve_init_options(args):
    prompt = _should_prompt_init(args)
    agent_default = "claude-cli"
    template_default = "blank"
    backlog_default = "github-issues"
    sandbox_default = "docker"
    agent = args.agent or (
        _prompt_choice("Coding agent", sorted(AGENT_REGISTRY), agent_default) if prompt else agent_default
    )
    template = args.template or (
        _prompt_choice("Template", [item.name for item in list_templates()], template_default)
        if prompt
        else template_default
    )
    backlog = args.backlog or (
        _prompt_choice("Backlog manager", sorted(BACKLOG_MANAGERS), backlog_default)
        if prompt
        else backlog_default
    )
    sandbox = args.sandbox or (
        _prompt_choice("Sandbox provider", sorted(SANDBOX_PROVIDERS), sandbox_default)
        if prompt
        else sandbox_default
    )
    model_default = AGENT_REGISTRY[agent].default_model
    model = args.model or (_prompt_text("Model", model_default) if prompt else None)
    create_label = args.create_label
    if create_label is None:
        create_label = _prompt_bool("Create SwarmBox GitHub label", True) if prompt else True
    build = args.build
    if build is None:
        build = _prompt_bool("Build sandbox image now", False) if prompt else False
    return {
        "agent_id": agent,
        "model": model,
        "template_name": template,
        "backlog_manager_name": backlog,
        "sandbox_provider_name": sandbox,
        "create_label": create_label,
        "build": build,
    }


def _cmd_init(args) -> int:
    options = _resolve_init_options(args)
    scaffold(
        args.cwd,
        agent_id=options["agent_id"],
        model=options["model"],
        template_name=options["template_name"],
        backlog_manager_name=options["backlog_manager_name"],
        sandbox_provider_name=options["sandbox_provider_name"],
        create_label=options["create_label"],
    )
    if options["build"]:
        _build_image(options["sandbox_provider_name"], args.cwd, args.image_name, None)
    print("SwarmBox initialized in .swarmbox/")
    return 0


def _build_image(kind: str, cwd: str, image_name: str = None, file_path: str = None) -> int:
    cwd_path = Path(cwd)
    image = image_name or default_image_name(str(cwd_path.resolve()))
    runtime = "docker" if kind == "docker" else "podman"
    containerfile = file_path or str(cwd_path / ".swarmbox" / ("Dockerfile" if kind == "docker" else "Containerfile"))
    context = str(cwd_path / ".swarmbox") if file_path is None else str(cwd_path)
    subprocess.check_call([runtime, "build", "-t", image, "-f", containerfile, context])
    return 0


def _cmd_build_image(kind: str):
    def inner(args):
        return _build_image(kind, args.cwd, args.image_name, args.file)

    return inner


def _cmd_remove_image(kind: str):
    def inner(args):
        image = args.image_name or default_image_name(str(Path(args.cwd).resolve()))
        runtime = "docker" if kind == "docker" else "podman"
        subprocess.check_call([runtime, "rmi", image])
        return 0

    return inner


def _cmd_agents_list(args) -> int:
    for meta in AGENT_REGISTRY.values():
        marker = "generic" if meta.generic else "first-class"
        print("%s\t%s\t%s\t%s" % (meta.id, meta.binary_name, marker, meta.default_model))
    return 0


def _cmd_agents_detect(args) -> int:
    found = []
    for meta in AGENT_REGISTRY.values():
        path = shutil.which(meta.binary_name)
        if path:
            found.append({"id": meta.id, "binary": meta.binary_name, "path": path})
    print(json.dumps(found, indent=2))
    return 0


def _cmd_agents_doctor(args) -> int:
    for meta in AGENT_REGISTRY.values():
        path = shutil.which(meta.binary_name)
        status = "ok" if path else "missing"
        mode = "generic" if meta.generic else "first-class"
        print("%-24s %-10s %-12s %s" % (meta.id, status, mode, path or meta.binary_name))
    return 0


def _cmd_templates_list(args) -> int:
    for template in list_templates():
        print("%s\t%s" % (template.name, template.description))
    return 0


def _cmd_run(args) -> int:
    provider = docker() if args.sandbox == "docker" else podman() if args.sandbox == "podman" else None
    if provider is None:
        from .sandbox import no_sandbox

        provider = no_sandbox()
    result = run(
        agent=agent_from_metadata(args.agent, model=args.model),
        sandbox=provider,
        cwd=args.cwd,
        prompt=args.prompt,
        prompt_file=args.prompt_file,
        max_iterations=args.max_iterations,
        name=args.name,
        logging={"type": "stdout"},
    )
    print(format_run_summary(result))
    print(json.dumps({"branch": result.branch, "commits": [c.sha for c in result.commits]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swarmbox")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init")
    init.add_argument("--cwd", default=".")
    init.add_argument("--agent", choices=sorted(AGENT_REGISTRY))
    init.add_argument("--model")
    init.add_argument("--template", choices=[item.name for item in list_templates()])
    init.add_argument("--backlog", choices=sorted(BACKLOG_MANAGERS))
    init.add_argument("--sandbox", choices=sorted(SANDBOX_PROVIDERS))
    init.add_argument("--image-name")
    init.add_argument("--interactive", action="store_true")
    init.add_argument("--yes", action="store_true")
    label = init.add_mutually_exclusive_group()
    label.add_argument("--label", dest="create_label", action="store_true")
    label.add_argument("--no-label", dest="create_label", action="store_false")
    init.set_defaults(create_label=None)
    build_group = init.add_mutually_exclusive_group()
    build_group.add_argument("--build", dest="build", action="store_true")
    build_group.add_argument("--no-build", dest="build", action="store_false")
    init.set_defaults(build=None)
    init.set_defaults(func=_cmd_init)

    for kind in ("docker", "podman"):
        parent = sub.add_parser(kind)
        parent_sub = parent.add_subparsers(dest="%s_command" % kind)
        build = parent_sub.add_parser("build-image")
        build.add_argument("--cwd", default=".")
        build.add_argument("--image-name")
        build.add_argument("--file")
        build.set_defaults(func=_cmd_build_image(kind))
        remove = parent_sub.add_parser("remove-image")
        remove.add_argument("--cwd", default=".")
        remove.add_argument("--image-name")
        remove.set_defaults(func=_cmd_remove_image(kind))

    agents = sub.add_parser("agents")
    agents_sub = agents.add_subparsers(dest="agents_command")
    agents_sub.add_parser("list").set_defaults(func=_cmd_agents_list)
    agents_sub.add_parser("detect").set_defaults(func=_cmd_agents_detect)
    agents_sub.add_parser("doctor").set_defaults(func=_cmd_agents_doctor)

    templates = sub.add_parser("templates")
    templates_sub = templates.add_subparsers(dest="templates_command")
    templates_sub.add_parser("list").set_defaults(func=_cmd_templates_list)

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--cwd", default=".")
    run_cmd.add_argument("--agent", default="codex-cli", choices=sorted(AGENT_REGISTRY))
    run_cmd.add_argument("--model")
    run_cmd.add_argument("--sandbox", default="docker", choices=["docker", "podman", "none"])
    run_cmd.add_argument("--prompt")
    run_cmd.add_argument("--prompt-file")
    run_cmd.add_argument("--max-iterations", type=int, default=1)
    run_cmd.add_argument("--name")
    run_cmd.set_defaults(func=_cmd_run)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except SwarmBoxError as exc:
        print(format_error(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

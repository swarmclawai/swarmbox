import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .agents import AGENT_REGISTRY, agent_from_metadata
from .api import run
from .init_service import scaffold
from .mounts import default_image_name
from .sandbox import docker, podman


def _cmd_init(args) -> int:
    scaffold(
        args.cwd,
        agent_id=args.agent,
        model=args.model,
        template_name=args.template,
        backlog_manager_name=args.backlog,
        sandbox_provider_name=args.sandbox,
        create_label=not args.no_label,
    )
    if args.build:
        _build_image(args.sandbox, args.cwd, args.image_name, None)
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
    print(json.dumps({"branch": result.branch, "commits": [c.sha for c in result.commits]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swarmbox")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init")
    init.add_argument("--cwd", default=".")
    init.add_argument("--agent", default="claude-cli", choices=sorted(AGENT_REGISTRY))
    init.add_argument("--model")
    init.add_argument("--template", default="blank")
    init.add_argument("--backlog", default="github-issues")
    init.add_argument("--sandbox", default="docker", choices=["docker", "podman"])
    init.add_argument("--image-name")
    init.add_argument("--no-label", action="store_true")
    init.add_argument("--no-build", dest="build", action="store_false")
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
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

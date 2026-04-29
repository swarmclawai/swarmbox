import os
from pathlib import Path
from typing import Dict


def parse_env_file(path: str) -> Dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    result: Dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        key, value = trimmed.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def resolve_env(repo_dir: str, config_dir: str = ".swarmbox") -> Dict[str, str]:
    repo = Path(repo_dir)
    env_file = repo / config_dir / ".env"
    declared = parse_env_file(str(env_file))
    resolved: Dict[str, str] = {}
    for key, value in declared.items():
        effective = value or os.environ.get(key)
        if effective:
            resolved[key] = effective
    return resolved

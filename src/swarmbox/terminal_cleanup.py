from __future__ import annotations

import atexit
import subprocess
from typing import Iterable, Set


_containers: Set[tuple[str, str]] = set()


def register_container(runtime: str, container_name: str) -> None:
    _containers.add((runtime, container_name))


def unregister_container(runtime: str, container_name: str) -> None:
    _containers.discard((runtime, container_name))


def registered_containers() -> Iterable[tuple[str, str]]:
    return tuple(_containers)


def cleanup_registered_containers() -> None:
    for runtime, name in tuple(_containers):
        subprocess.call([runtime, "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unregister_container(runtime, name)


atexit.register(cleanup_registered_containers)

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Union

from .errors import PromptArgError, PromptError

PromptArgValue = Union[str, int, bool]
PromptArgs = Mapping[str, PromptArgValue]

BUILT_IN_PROMPT_ARG_KEYS = ("SOURCE_BRANCH", "TARGET_BRANCH")
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SHELL_BLOCK_MARKER = "\x01"
SHELL_BLOCK_PATTERN = re.compile(r"!`([^`]+)`")
MARKED_SHELL_BLOCK_PATTERN = re.compile(r"!\x01`([^`]+)`")


@dataclass(frozen=True)
class ResolvedPrompt:
    text: str
    source: str


def validate_no_args_with_inline_prompt(args: PromptArgs) -> None:
    if args:
        raise PromptArgError(
            'prompt_args is only supported with prompt_file. Inline prompts are passed '
            "to the agent as-is."
        )


def validate_no_builtin_arg_override(args: PromptArgs) -> None:
    for key in BUILT_IN_PROMPT_ARG_KEYS:
        if key in args:
            raise PromptArgError(
                '"%s" is a built-in prompt argument and cannot be overridden via prompt_args'
                % key
            )


def find_missing_prompt_arg_keys(prompt: str, provided_args: PromptArgs) -> List[str]:
    seen = set()
    missing: List[str] = []
    builtin = set(BUILT_IN_PROMPT_ARG_KEYS)
    for match in PLACEHOLDER_PATTERN.finditer(prompt):
        key = match.group(1)
        if key in seen:
            continue
        seen.add(key)
        if key in builtin or key in provided_args:
            continue
        missing.append(key)
    return missing


def collect_missing_prompt_args(
    prompt: str,
    provided_args: Optional[PromptArgs] = None,
    input_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, PromptArgValue]:
    args: Dict[str, PromptArgValue] = dict(provided_args or {})
    ask = input_fn or input
    for key in find_missing_prompt_arg_keys(prompt, args):
        args[key] = ask("Enter value for {{%s}}: " % key)
    return args


def _mark_shell_blocks(prompt: str) -> str:
    clean = prompt.replace(SHELL_BLOCK_MARKER, "")
    return SHELL_BLOCK_PATTERN.sub(lambda m: "!%s`%s`" % (SHELL_BLOCK_MARKER, m.group(1)), clean)


def substitute_prompt_args(
    prompt: str,
    args: PromptArgs,
    builtin_args: Optional[Mapping[str, PromptArgValue]] = None,
    warn_unused: Optional[callable] = None,
) -> str:
    validate_no_builtin_arg_override(args)
    effective: Dict[str, PromptArgValue] = {}
    if builtin_args:
        effective.update(builtin_args)
    sanitized_args: Dict[str, PromptArgValue] = {}
    for key, value in args.items():
        if isinstance(value, str):
            sanitized_args[key] = value.replace(SHELL_BLOCK_MARKER, "")
        else:
            sanitized_args[key] = value
    effective.update(sanitized_args)

    marked_prompt = _mark_shell_blocks(prompt)
    matches = list(PLACEHOLDER_PATTERN.finditer(marked_prompt))
    referenced = {m.group(1) for m in matches}
    for key in referenced:
        if key not in effective:
            raise PromptArgError('Prompt argument "{{%s}}" has no matching value in prompt_args' % key)
    silent = set(BUILT_IN_PROMPT_ARG_KEYS)
    if warn_unused:
        for key in sanitized_args:
            if key not in referenced and key not in silent:
                warn_unused('Prompt argument "%s" was provided but not referenced in the prompt' % key)

    return PLACEHOLDER_PATTERN.sub(lambda m: str(effective[m.group(1)]), marked_prompt)


def resolve_prompt(
    prompt: Optional[str] = None,
    prompt_file: Optional[str] = None,
    prompt_args: Optional[PromptArgs] = None,
    builtin_args: Optional[Mapping[str, PromptArgValue]] = None,
    allow_empty: bool = False,
) -> ResolvedPrompt:
    if prompt is not None and prompt_file is not None:
        raise PromptError("prompt and prompt_file are mutually exclusive")
    args = prompt_args or {}
    if prompt is not None:
        validate_no_args_with_inline_prompt(args)
        return ResolvedPrompt(prompt, "inline")
    if prompt_file is not None:
        raw = Path(prompt_file).read_text(encoding="utf-8")
        return ResolvedPrompt(substitute_prompt_args(raw, args, builtin_args=builtin_args), "file")
    if allow_empty:
        return ResolvedPrompt("", "none")
    raise PromptError("Either prompt or prompt_file is required")


def preprocess_prompt(prompt: str, sandbox, cwd: str) -> str:
    matches = list(MARKED_SHELL_BLOCK_PATTERN.finditer(prompt))
    if not matches:
        return prompt.replace(SHELL_BLOCK_MARKER, "")
    replacements: List[str] = []
    for match in matches:
        command = match.group(1)
        result = sandbox.exec(command, cwd=cwd)
        exit_code = getattr(result, "exit_code", getattr(result, "exitCode", 0))
        if exit_code != 0:
            stderr = getattr(result, "stderr", "")
            raise PromptError("Command `%s` exited with code %s: %s" % (command, exit_code, stderr))
        replacements.append(getattr(result, "stdout", "").rstrip("\n"))
    output = prompt
    for match, replacement in reversed(list(zip(matches, replacements))):
        output = output[: match.start()] + replacement + output[match.end() :]
    return output.replace(SHELL_BLOCK_MARKER, "")


def placeholder_keys(prompt: str) -> Iterable[str]:
    seen = set()
    for match in PLACEHOLDER_PATTERN.finditer(prompt):
        key = match.group(1)
        if key not in seen:
            seen.add(key)
            yield key

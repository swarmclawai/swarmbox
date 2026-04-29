import json
import shlex
from typing import Dict, Iterable, List, Optional, Sequence

from .models import AgentMetadata, AgentProvider, IterationUsage, ParsedStreamEvent, PrintCommand


def _json(line: str):
    try:
        return json.loads(line)
    except Exception:
        return None


def _events_from_text(text: str) -> Iterable[ParsedStreamEvent]:
    if text:
        yield ParsedStreamEvent(type="text", text=text)


def _format_tool_args(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "command" in value and len(value) == 1:
        return str(value["command"])
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _parse_codex_line(line: str) -> Iterable[ParsedStreamEvent]:
    obj = _json(line)
    if not isinstance(obj, dict):
        return []
    events: List[ParsedStreamEvent] = []
    item = obj.get("item") if isinstance(obj.get("item"), dict) else obj
    event_type = obj.get("type") or item.get("type")
    if event_type in ("agent_message", "message", "response.output_text.delta"):
        text = item.get("text") or item.get("message") or obj.get("delta")
        if text:
            events.append(ParsedStreamEvent(type="text", text=str(text)))
    if event_type in ("item.completed", "agent_message"):
        text = item.get("text") or item.get("message")
        if text:
            events.append(ParsedStreamEvent(type="result", result=str(text)))
    if event_type in ("command_execution", "tool_call"):
        name = item.get("name") or item.get("command") or "tool"
        args = item.get("args") or item.get("arguments") or item.get("command")
        events.append(ParsedStreamEvent(type="tool_call", name=str(name), args=_format_tool_args(args)))
    return events


def _parse_claude_line(line: str) -> Iterable[ParsedStreamEvent]:
    obj = _json(line)
    if not isinstance(obj, dict):
        return []
    events: List[ParsedStreamEvent] = []
    if obj.get("type") == "system" and obj.get("subtype") == "init" and obj.get("session_id"):
        events.append(ParsedStreamEvent(type="session_id", session_id=str(obj["session_id"])))
    if obj.get("type") == "assistant":
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        for part in message.get("content", []) or []:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                events.append(ParsedStreamEvent(type="text", text=str(part["text"])))
            if isinstance(part, dict) and part.get("type") == "tool_use":
                events.append(
                    ParsedStreamEvent(
                        type="tool_call",
                        name=str(part.get("name", "tool")),
                        args=_format_tool_args(part.get("input")),
                    )
                )
    if obj.get("type") == "result":
        result = obj.get("result") or obj.get("text")
        if result:
            events.append(ParsedStreamEvent(type="result", result=str(result)))
    return events


def _parse_pi_line(line: str) -> Iterable[ParsedStreamEvent]:
    obj = _json(line)
    if not isinstance(obj, dict):
        return _events_from_text(line)
    event_type = obj.get("type")
    if event_type == "message_update":
        event = obj.get("assistantMessageEvent")
        if isinstance(event, dict) and event.get("type") == "text_delta" and event.get("delta"):
            return [ParsedStreamEvent(type="text", text=str(event["delta"]))]
    if event_type == "tool_execution_start":
        name = obj.get("toolName")
        if name in ("Bash", "Edit", "MultiEdit", "Read", "Write", "Glob", "Grep", "LS"):
            return [
                ParsedStreamEvent(
                    type="tool_call",
                    name=str(name),
                    args=_format_tool_args(obj.get("args")),
                )
            ]
        return []
    if event_type == "agent_end":
        messages = obj.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                text_parts = [
                    part.get("text")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
                ]
                if text_parts:
                    return [ParsedStreamEvent(type="result", result="\n".join(str(part) for part in text_parts))]
    text = obj.get("text") or obj.get("delta") or obj.get("message")
    if text:
        return [ParsedStreamEvent(type="text", text=str(text))]
    if obj.get("result"):
        return [ParsedStreamEvent(type="result", result=str(obj["result"]))]
    return []


def _parse_claude_usage(content: str) -> Optional[IterationUsage]:
    last_usage = None
    for line in content.splitlines():
        if not line:
            continue
        obj = _json(line)
        if not isinstance(obj, dict):
            continue
        message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        if usage:
            last_usage = usage
    if not last_usage:
        return None
    return IterationUsage(
        input_tokens=last_usage.get("input_tokens"),
        output_tokens=last_usage.get("output_tokens"),
        cache_creation_input_tokens=last_usage.get("cache_creation_input_tokens"),
        cache_read_input_tokens=last_usage.get("cache_read_input_tokens"),
    )


def codex(model: str, effort: Optional[str] = None) -> AgentProvider:
    def build(options):
        parts = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            model,
        ]
        if effort:
            parts.extend(["-c", 'model_reasoning_effort="%s"' % effort])
        return PrintCommand(command=shlex.join(parts), stdin=options.prompt)

    return AgentProvider("codex", build, parse_stream_line_fn=_parse_codex_line)


def claude_code(
    model: str,
    effort: Optional[str] = None,
    capture_sessions: bool = True,
) -> AgentProvider:
    def build(options):
        parts = [
            "claude",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--model",
            model,
        ]
        if options.dangerously_skip_permissions:
            parts.append("--dangerously-skip-permissions")
        if effort:
            parts.extend(["--effort", effort])
        if options.resume_session:
            parts.extend(["--resume", options.resume_session])
        parts.extend(["-p", "-"])
        return PrintCommand(command=shlex.join(parts), stdin=options.prompt)

    def interactive(options):
        parts = ["claude", "--model", model]
        if effort:
            parts.extend(["--effort", effort])
        if options.dangerously_skip_permissions:
            parts.append("--dangerously-skip-permissions")
        if options.resume_session:
            parts.extend(["--resume", options.resume_session])
        if options.prompt:
            parts.extend(["-p", options.prompt])
        return parts

    return AgentProvider(
        "claude-code",
        build,
        capture_sessions=capture_sessions,
        build_interactive_args_fn=interactive,
        parse_stream_line_fn=_parse_claude_line,
        parse_session_usage_fn=_parse_claude_usage,
    )


claudeCode = claude_code


def opencode(model: str) -> AgentProvider:
    def build(options):
        return PrintCommand(command=shlex.join(["opencode", "run", "--model", model, options.prompt]))

    def interactive(options):
        return ["opencode", "--model", model, "-p", options.prompt]

    return AgentProvider("opencode", build, build_interactive_args_fn=interactive)


def pi(model: str) -> AgentProvider:
    def build(options):
        return PrintCommand(
            command=shlex.join(["pi", "-p", "--mode", "json", "--no-session", "--model", model]),
            stdin=options.prompt,
        )

    return AgentProvider("pi", build, parse_stream_line_fn=_parse_pi_line)


def command_agent(
    name: str,
    command_template: Sequence[str],
    model: str = "default",
    env: Optional[Dict[str, str]] = None,
    supports_interactive: bool = False,
) -> AgentProvider:
    def render(template: Sequence[str], prompt: str) -> List[str]:
        return [part.format(prompt=prompt, model=model) for part in template]

    def build(options):
        return PrintCommand(command=shlex.join(render(command_template, options.prompt)))

    def interactive(options):
        return render(command_template, options.prompt)

    return AgentProvider(
        name=name,
        env=env or {},
        build_print_command_fn=build,
        build_interactive_args_fn=interactive if supports_interactive else None,
    )


def _metadata(
    id: str,
    display_name: str,
    binary_name: str,
    capability: str,
    description: Optional[str] = None,
    default_model: str = "default",
    generic: bool = True,
    auth_backend: Optional[str] = None,
    optional_api_key: bool = True,
    model_library_url: Optional[str] = None,
) -> AgentMetadata:
    return AgentMetadata(
        id=id,
        display_name=display_name,
        binary_name=binary_name,
        capability=capability,
        description=description or ("%s: %s." % (display_name, capability)),
        default_model=default_model,
        generic=generic,
        optional_api_key=optional_api_key,
        auth_backend=auth_backend,
        model_library_url=model_library_url,
    )


_BESPOKE = [
    _metadata("claude-cli", "Claude Code CLI", "claude", "multi-file code editing, refactoring, debugging, code review", default_model="claude-sonnet-4-6", generic=False, auth_backend="claude", model_library_url="https://docs.anthropic.com/en/docs/about-claude/models"),
    _metadata("codex-cli", "OpenAI Codex CLI", "codex", "code generation, file creation, automated coding tasks", default_model="gpt-5.4-codex", generic=False, auth_backend="codex", model_library_url="https://platform.openai.com/docs/models"),
    _metadata("opencode-cli", "OpenCode CLI", "opencode", "code analysis, generation across multiple LLM backends", default_model="claude-sonnet-4-6", generic=False, auth_backend="opencode"),
    _metadata("gemini-cli", "Gemini CLI", "gemini", "code generation, analysis with Gemini models", default_model="gemini-3.1-pro", generic=False, auth_backend="gemini", model_library_url="https://ai.google.dev/gemini-api/docs/models"),
    _metadata("copilot-cli", "GitHub Copilot CLI", "copilot", "code generation, analysis, multi-model support via GitHub Copilot", default_model="claude-sonnet-4-6", generic=False, auth_backend="copilot"),
    _metadata("droid-cli", "Factory Droid CLI", "droid", "code generation, refactoring, and automation via Factory Droid", generic=False, auth_backend="droid"),
    _metadata("cursor-cli", "Cursor Agent CLI", "cursor-agent", "full-agent coding workflows, multi-file edits", default_model="auto", generic=False, auth_backend="cursor"),
    _metadata("qwen-code-cli", "Qwen Code CLI", "qwen", "terminal-native coding workflows, code generation, review, and automation", generic=False, auth_backend="qwen"),
    _metadata("goose", "Goose", "goose", "agentic coding workflows with extensions, tools, and runtime-managed execution", generic=False, auth_backend="goose"),
    _metadata("pi", "Pi", "pi", "JSON-streaming coding agent for headless task runs", default_model="claude-sonnet-4-6", generic=False, auth_backend="anthropic"),
]

_GENERIC_ROWS = [
    ("aider-cli", "Aider CLI", "aider", "paired-programming-style multi-file edits and git-aware code changes"),
    ("amp-cli", "Amp CLI", "amp", "agentic coding via Sourcegraph Amp"),
    ("augment-cli", "Augment CLI", "augment", "codebase-aware agentic edits via Augment"),
    ("adal-cli", "AdaL CLI", "adal", "AdaL coding agent for terminal-driven workflows"),
    ("bob-cli", "IBM Bob CLI", "bob", "IBM watsonx Code Assistant terminal coding workflows"),
    ("cline-cli", "Cline CLI", "cline", "autonomous file-level edits and terminal automation via Cline"),
    ("codebuddy-cli", "CodeBuddy CLI", "codebuddy", "CodeBuddy agentic coding workflows"),
    ("command-code-cli", "Command Code CLI", "commandcode", "Command Code terminal-native coding agent"),
    ("continue-cli", "Continue CLI", "continue", "agentic coding via the Continue CLI"),
    ("cortex-cli", "Cortex Code CLI", "cortex", "Snowflake Cortex Code agentic workflows"),
    ("crush-cli", "Crush CLI", "crush", "Crush terminal coding agent"),
    ("deepagents-cli", "Deep Agents CLI", "deepagents", "long-horizon planning and multi-step coding via Deep Agents"),
    ("firebender-cli", "Firebender CLI", "firebender", "Firebender JetBrains-aligned coding agent"),
    ("iflow-cli", "iFlow CLI", "iflow", "iFlow CLI agentic coding workflows"),
    ("junie-cli", "Junie CLI", "junie", "JetBrains Junie coding agent for terminal use"),
    ("kilo-code-cli", "Kilo Code CLI", "kilocode", "Kilo Code agentic coding workflows"),
    ("kimi-cli", "Kimi CLI", "kimi", "Kimi Code CLI coding agent"),
    ("kode-cli", "Kode CLI", "kode", "Kode terminal coding agent"),
    ("mcpjam-cli", "MCPJam CLI", "mcpjam", "MCPJam-tooled agentic coding workflows"),
    ("mistral-vibe-cli", "Mistral Vibe CLI", "vibe", "Mistral Vibe coding agent"),
    ("mux-cli", "Mux CLI", "mux", "Mux multi-tool coding agent"),
    ("neovate-cli", "Neovate CLI", "neovate", "Neovate coding agent for terminal workflows"),
    ("openhands-cli", "OpenHands CLI", "openhands", "OpenHands agentic coding via terminal"),
    ("pochi-cli", "Pochi CLI", "pochi", "Pochi coding agent"),
    ("qoder-cli", "Qoder CLI", "qoder", "Qoder agentic coding workflows"),
    ("replit-cli", "Replit Agent CLI", "replit", "Replit Agent terminal coding workflows"),
    ("roo-code-cli", "Roo Code CLI", "roo", "Roo Code agentic coding workflows"),
    ("trae-cli", "Trae CLI", "trae", "Trae coding agent workflows"),
    ("claw-cli", "Claw CLI", "claw", "Claw/OpenClaw coding agent workflows"),
    ("kiro-cli", "Kiro CLI", "kiro", "Kiro coding agent workflows"),
    ("hermes-cli", "Hermes CLI", "hermes", "Hermes coding agent workflows"),
    ("antigravity-cli", "Google Antigravity", "antigravity", "Google Antigravity coding workflows"),
    ("vscode-copilot-chat-cli", "VS Code Copilot Chat", "code", "VS Code Copilot Chat instruction workflow"),
    ("openclaw-cli", "OpenClaw CLI", "openclaw", "OpenClaw coding agent workflows"),
    ("trae-cn-cli", "TRAE CN CLI", "trae-cn", "TRAE CN coding agent"),
    ("warp-cli", "Warp Agent CLI", "warp", "Warp Agent terminal-native coding workflows"),
    ("windsurf-cli", "Windsurf CLI", "windsurf", "Windsurf agentic coding workflows"),
    ("zencoder-cli", "Zencoder CLI", "zencoder", "Zencoder agentic coding workflows"),
]

AGENT_REGISTRY: Dict[str, AgentMetadata] = {
    item.id: item for item in _BESPOKE + [_metadata(*row) for row in _GENERIC_ROWS]
}


def agent_from_metadata(agent_id: str, model: Optional[str] = None) -> AgentProvider:
    meta = AGENT_REGISTRY[agent_id]
    chosen_model = model or meta.default_model
    if agent_id == "claude-cli":
        return claude_code(chosen_model)
    if agent_id == "codex-cli":
        return codex(chosen_model)
    if agent_id == "opencode-cli":
        return opencode(chosen_model)
    if agent_id == "pi":
        return pi(chosen_model)
    return command_agent(
        name=meta.id,
        command_template=[meta.binary_name, "{prompt}"],
        model=chosen_model,
        supports_interactive=True,
    )

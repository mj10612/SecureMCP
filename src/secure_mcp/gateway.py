"""Loopback subscription proxy. CLI-owned OAuth is forwarded, never loaded from disk.

Only inference endpoints are proxied. Unknown/binary payloads fail closed. SSE is
buffered so aliases split across events can be restored before local execution.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
from threading import RLock
from typing import Any
import urllib.error
import urllib.request

from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.engine.strategies import mapping_key
from secure_mcp.encrypted_session import load_session, save_session
from secure_mcp.hooks import _atomic_json
from secure_mcp.models import MaskMode, SurrogateStrategy, TokenMapping, TokenType
from secure_mcp.session import PrivacySession

LIMIT = 16 * 1024 * 1024
EXEC_GRAMMAR = r"""
start: pragma_source | plain_source
pragma_source: PRAGMA_LINE NEWLINE SOURCE
plain_source: SOURCE

PRAGMA_LINE: /[ \t]*\/\/ @exec:[^\r\n]*/
NEWLINE: /\r?\n/
SOURCE: /[\s\S]+/
"""
# Pinned OpenAI public apply_patch grammar, with/without environment selection.
PATCH_GRAMMAR_HASHES = {
    "50b06e74592ca36baf0a0804a36d76aef1847b251c7b5b5bfd9bc81ec7794023",
    "1f1bb6528b7ab190a5542bc710053077515f5f800cd58e77f141820566ca4f20",
}
TASK_WORDS = {
    "review",
    "inspect",
    "analyze",
    "explain",
    "fix",
    "implement",
    "read",
    "search",
    "find",
    "summarize",
    "compare",
    "refactor",
    "test",
    "check",
    "show",
    "repeat",
    "reply",
    "exactly",
    "identifier",
    "function",
    "code",
    "file",
    "directory",
    "bug",
    "security",
    "syntax",
    "error",
    "issue",
    "result",
    "please",
    "검토",
    "검토해줘",
    "봐줘",
    "설명",
    "설명해줘",
    "고쳐줘",
    "함수",
    "코드",
    "파일",
    "보안",
    "오류",
    "수정",
    "분석",
    "분석해줘",
}
PRIVACY_INSTRUCTION = (
    "The following context was masked locally. Names and values beginning smcp_ID_ "
    "and numeric aliases are opaque. Keep every supplied alias exactly unchanged in "
    "answers and tool arguments; a local host restores them. Never invent aliases or "
    "guess originals. Use public names for newly introduced identifiers. State when "
    "hidden literal values prevent analysis.\n"
)
PUBLIC_CLAUDE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
PUBLIC_CLAUDE_IDENTITIES = (
    PUBLIC_CLAUDE_IDENTITY,
    "You are a Claude agent, built on Anthropic's Claude Agent SDK.",
)
CLAUDE_ATTRIBUTION_PREFIX = "x-anthropic-billing-header:"
# This positional protocol block is consumed by Anthropic before model inference.
# Pin its observed fields rather than passing arbitrary system text through.
CLAUDE_ATTRIBUTION = re.compile(
    r"x-anthropic-billing-header: cc_version=[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}"
    r"(?:\.[0-9a-f]{3})?; cc_entrypoint=(?:cli|sdk-cli);"
    r"(?: cch=[0-9a-f]{6,64};)?[ \t]*"
)
TOOL_DESCRIPTIONS = {
    "Read": "Read a local file using file_path.",
    "Bash": "Run a local shell command using command.",
    "PowerShell": "Run a local PowerShell command.",
    "exec_command": "Run a local shell command using cmd; return output and session_id.",
    "write_stdin": "Send chars to a running session_id, or poll for output.",
    "Write": "Write content into a local file using file_path.",
    "Edit": "Replace old_string with new_string in file_path.",
    "Grep": "Search local file contents for pattern.",
    "Glob": "Find local files matching pattern.",
    "apply_patch": "Apply a patch to local files using the supplied patch grammar.",
}
UPSTREAMS = {
    "claude": "https://api.anthropic.com",
    "codex": "https://chatgpt.com/backend-api/codex",
}
PROTOCOL = {
    "type",
    "role",
    "id",
    "call_id",
    "tool_use_id",
    "model",
    "status",
    "stop_reason",
    "object",
    "effort",
    "verbosity",
    "syntax",
}
TOOL_NAMES = {
    "Bash",
    "PowerShell",
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "MultiEdit",
    "TodoWrite",
    "Task",
    "Agent",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "exec_command",
    "write_stdin",
    "apply_patch",
    "update_plan",
    "request_user_input",
    "functions",
    "exec",
    "parallel",
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
    "view_image",
    "spawn_agent",
    "send_input",
    "wait",
    "close_agent",
}
TOP_FIELDS = {
    "model",
    "messages",
    "system",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "reasoning",
    "stream",
    "store",
    "max_output_tokens",
    "max_tokens",
    "include",
    "metadata",
    "text",
    "prompt_cache_key",
    "safety_identifier",
    "service_tier",
    "temperature",
    "top_p",
    "top_k",
    "truncation",
    "background",
    "stop_sequences",
    "thinking",
    "output_config",
    "context_management",
    "client_metadata",
    "safeguards",
}
BLOCKED_TYPES = {
    "image",
    "document",
    "input_image",
    "input_file",
    "input_audio",
    "computer",
    "computer_20250124",
    "computer_20251124",
}
ARGUMENT_KEYS = {
    "cmd",
    "command",
    "path",
    "file_path",
    "filePath",
    "content",
    "text",
    "old_string",
    "new_string",
    "pattern",
    "query",
    "yield_time_ms",
    "max_output_tokens",
    "session_id",
    "chars",
    "workdir",
    "timeout",
    "description",
    "shell",
    "login",
    "tty",
    "justification",
    "sandbox_permissions",
    "prefix_rule",
    "input",
    "patch",
}


class SharedEngine(MaskingEngine):
    """One alias for an original in prompts, code, paths and tool results."""

    def _get_or_create_surrogate(
        self,
        original_token,
        token_type,
        generator,
        mapping_store,
        reverse_store,
        code=False,
    ):
        with generator.operation():
            kind = (
                TokenType.NUMBER
                if token_type == TokenType.NUMBER
                else TokenType.IDENTIFIER
            )
            key = mapping_key(original_token, kind, code=True)
            if key in mapping_store:
                mapping_store[key].occurrence_count += 1
                return mapping_store[key].surrogate
            surrogate = generator.generate(kind, original_token, code=True)
            mapping = TokenMapping(
                original=original_token,
                surrogate=surrogate,
                token_type=kind,
                context="code",
            )
            mapping_store[key] = mapping
            reverse_store[surrogate] = mapping
            return surrogate


class GatewaySession:
    def __init__(self, session=None):
        self.session = session or PrivacySession(
            "gateway", strategy=SurrogateStrategy.UNICODE, ttl_seconds=86400
        )
        self.engine = SharedEngine()
        self.lock = RLock()
        self.opaque: set[str] = set()

    def _opaque_key(self, block):
        stable = {
            key: value for key, value in block.items() if key not in {"id", "status"}
        }
        return sha256(
            json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def mask(self, text: str, code: bool = False, language: str = "auto") -> str:
        s = self.session
        if len(s.forward_store) > 100_000:
            raise ValueError(
                "Mapping limit reached; start a new local gateway session."
            )
        if code:
            return self.engine.mask_code(
                text,
                s.session_id,
                s.generator,
                s.forward_store,
                s.reverse_store,
                language=language,
            ).masked_text
        if "```" not in text and re.search(
            r"(?m)^\s*(?:async\s+def\s|def\s|class\s|function\s|const\s|let\s|var\s|package\s|func\s|fn\s|pub\s+fn\s|#\s*include\b|SELECT\s|[\w]+\s*=(?!=))",
            text,
        ):
            return self.mask(text, code=True)
        # Fenced/inline source is lexed, rather than replaced as an opaque block.
        parts = re.split(
            r"(```[^\n]*\n[\s\S]*?```|`[^`\n]+`|"
            + "|".join(re.escape(identity) for identity in PUBLIC_CLAUDE_IDENTITIES)
            + ")",
            text,
        )
        output = []
        for part in parts:
            if part in PUBLIC_CLAUDE_IDENTITIES:
                output.append(part)
                continue
            if part.startswith("```") and "\n" in part and part.endswith("```"):
                header, body = part.split("\n", 1)
                if header[3:].strip().lower() not in {
                    "python",
                    "py",
                    "javascript",
                    "js",
                    "typescript",
                    "ts",
                    "go",
                    "rust",
                    "rs",
                    "java",
                    "c",
                    "cpp",
                    "c++",
                    "sql",
                    "",
                }:
                    header = "```"
                output.append(
                    header
                    + "\n"
                    + self.mask(
                        body[:-3], code=True, language=header[3:].strip() or "auto"
                    )
                    + "```"
                )
            elif part.startswith("`") and part.endswith("`") and len(part) > 1:
                output.append("`" + self.mask(part[1:-1], code=True) + "`")
            else:
                # Force capitalized single-letter names such as A (an English article).
                terms = {m.original for m in s.forward_store.values()}
                terms.update(re.findall(r"\b[A-Z]\b", part))
                output.append(
                    self.engine.mask_text(
                        part,
                        s.session_id,
                        s.generator,
                        s.forward_store,
                        s.reverse_store,
                        mode=MaskMode.CONTENT_WORDS,
                        sensitive_terms=terms,
                        custom_preserve=TASK_WORDS,
                    ).masked_text
                )
        return "".join(output)

    def restore(self, text):
        s = self.session
        return self.engine.unmask(
            text, s.session_id, s.reverse_store, s.strategy, strict=True
        ).unmasked_text

    def values(self, value, restore=False, code=False):
        operation = self.restore if restore else lambda text: self.mask(text, code=code)
        if isinstance(value, str):
            return operation(value)
        if isinstance(value, list):
            return [self.values(item, restore, code) for item in value]
        if isinstance(value, dict):
            return {
                (key if key in ARGUMENT_KEYS else operation(key)): self.values(
                    item, restore, code
                )
                for key, item in value.items()
            }
        return value

    def walk(self, value, restore=False, code=False):
        if isinstance(value, str):
            return self.restore(value) if restore else self.mask(value, code)
        if isinstance(value, list):
            return [self.walk(item, restore, code) for item in value]
        if not isinstance(value, dict):
            return value
        kind = value.get("type", "")
        if not isinstance(kind, str):
            kind = ""
        if kind in BLOCKED_TYPES or any(
            k in value for k in ("image_url", "file_data", "file_id", "data")
        ):
            raise ValueError(
                "Binary/remote attachments are unsupported by the privacy gateway."
            )
        if kind in {"thinking", "redacted_thinking", "reasoning"}:
            key = self._opaque_key(value)
            if restore:
                self.opaque.add(key)
            elif key not in self.opaque:
                raise ValueError(
                    "Unrecognized signed/encrypted reasoning; start a new conversation."
                )
            return deepcopy(value)
        if any(key in value for key in {"signature", "encrypted_content"}):
            raise ValueError(
                "Opaque fields outside recognized reasoning are unsupported."
            )
        result = {}
        for key, item in value.items():
            if key in {"name", "tool_name"}:
                result[key] = (
                    self.restore(item)
                    if restore
                    else item
                    if item in TOOL_NAMES
                    else self.mask(item, code=True)
                )
            elif (
                key == "format"
                and kind == "custom"
                and isinstance(item, dict)
                and item.get("type") == "grammar"
            ):
                definition = item.get("definition", "")
                normalized = definition.replace("\r\n", "\n").strip()
                allowed = (
                    value.get("name") == "exec"
                    and normalized == EXEC_GRAMMAR.strip()
                    or value.get("name") == "apply_patch"
                    and sha256(normalized.encode()).hexdigest() in PATCH_GRAMMAR_HASHES
                )
                if not allowed:
                    raise ValueError(
                        "Unrecognized custom tool grammar; request was not forwarded."
                    )
                result[key] = deepcopy(
                    item
                )  # Only pinned public CLI grammars may pass unchanged.
            elif (
                key == "format"
                and isinstance(item, str)
                and item
                in {
                    "uri",
                    "uri-reference",
                    "date",
                    "time",
                    "date-time",
                    "duration",
                    "uuid",
                    "email",
                    "hostname",
                    "ipv4",
                    "ipv6",
                    "regex",
                }
            ):
                result[key] = item
            elif key in PROTOCOL or key == "cache_control":
                result[key] = deepcopy(item)
            elif key == "arguments" and isinstance(item, str):
                if restore and not item:
                    result[key] = ""
                    continue
                result[key] = json.dumps(
                    self.values(json.loads(item), restore, code=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            elif key == "input" and kind == "custom_tool_call":
                result[key] = self.walk(item, restore, code=True)
            elif key == "input" and kind == "tool_use":
                result[key] = self.values(item, restore, code=True)
            elif key == "output" and kind in {
                "function_call_output",
                "custom_tool_call_output",
            }:
                result[key] = self.walk(item, restore, code=True)
            elif key == "content" and kind == "tool_result":
                result[key] = self.walk(item, restore, code=True)
            elif key in {"properties", "$defs", "definitions"}:
                result[key] = {
                    (
                        self.restore(k)
                        if restore
                        else k
                        if k in ARGUMENT_KEYS
                        else self.mask(k, code=True)
                    ): self.walk(v, restore)
                    for k, v in item.items()
                }
            elif key == "required":
                result[key] = [
                    self.restore(k)
                    if restore
                    else k
                    if k in ARGUMENT_KEYS
                    else self.mask(k, code=True)
                    for k in item
                ]
            elif key == "$ref" and isinstance(item, str):
                if not item.startswith("#/"):
                    raise ValueError("Remote schema references are unsupported.")
                segments = item[2:].split("/")
                transformed = []
                for segment in segments:
                    decoded = segment.replace("~1", "/").replace("~0", "~")
                    if decoded not in {"$defs", "definitions", "properties", "items"}:
                        decoded = (
                            self.restore(decoded)
                            if restore
                            else decoded
                            if decoded in ARGUMENT_KEYS
                            else self.mask(decoded, code=True)
                        )
                    transformed.append(decoded.replace("~", "~0").replace("/", "~1"))
                result[key] = "#/" + "/".join(transformed)
            elif key in {"additionalProperties", "strict", "enum"}:
                result[key] = self.walk(item, restore, code)
            else:
                result[key] = self.walk(item, restore, code)
        return result

    def safeguards(self, value):
        """Keep safety-policy enums usable while anonymizing paths/rule operands."""
        if isinstance(value, list):
            return [self.safeguards(item) for item in value]
        if not isinstance(value, dict):
            return self.mask(value) if isinstance(value, str) else value
        result = {}
        for key, item in value.items():
            if key in {"type", "permission_mode", "platform", "source"}:
                # Public classifier protocol tags; never arbitrary private content.
                if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z_]+", item):
                    raise ValueError("Unsupported safety protocol tag.")
                result[key] = item
            elif key == "rule" and item in TOOL_NAMES:
                result[key] = item
            else:
                result[key] = self.safeguards(item)
        return result

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - TOP_FIELDS:
            raise ValueError("Unsupported request fields; request was not forwarded.")
        if payload.get("background") or payload.get("truncation") == "auto":
            raise ValueError("Background/server-side truncation is unsupported.")

        # Seed code aliases before prompt references in this request.
        def seed(value):
            if isinstance(value, str):
                self.mask(value)
            elif isinstance(value, list):
                for item in value:
                    seed(item)
            elif isinstance(value, dict):
                if value.get("type") in {
                    "tool_result",
                    "function_call_output",
                    "tool_use",
                    "function_call",
                    "custom_tool_call",
                    "custom_tool_call_output",
                }:
                    self.walk(value)
                else:
                    for item in value.values():
                        seed(item)

        seed(payload.get("messages", payload.get("input", [])))
        result = {}
        for key, value in payload.items():
            if key == "system" and "messages" in payload:
                result[key] = self.claude_system(value)
                continue
            if key == "safeguards":
                result[key] = self.safeguards(value)
                continue
            if key in {"client_metadata", "metadata"}:
                continue  # Optional diagnostics are not required for inference.
            if key == "tool_choice" and isinstance(value, str):
                if value not in {"auto", "none", "required", "any"}:
                    raise ValueError("Unsupported tool choice.")
                result[key] = value
                continue
            if key in {
                "messages",
                "input",
                "instructions",
                "system",
                "tools",
                "text",
                "stop_sequences",
                "tool_choice",
                "output_config",
                "context_management",
                "safeguards",
            }:
                result[key] = self.walk(value)
            elif key in {"prompt_cache_key", "safety_identifier"}:
                result[key] = sha256(str(value).encode()).hexdigest()
            else:
                result[key] = deepcopy(value)
        if "input" in result:
            result["store"] = False
            result["instructions"] = PRIVACY_INSTRUCTION + result.get(
                "instructions", ""
            )
        elif "messages" in result:
            system = result.get("system", [])
            if isinstance(system, str):
                system = [{"type": "text", "text": system}]
            result["system"] = [*system, {"type": "text", "text": PRIVACY_INSTRUCTION}]
        for tool in result.get("tools", []):
            if tool.get("name") in TOOL_DESCRIPTIONS:
                tool["description"] = TOOL_DESCRIPTIONS[tool["name"]]
        return result

    def claude_system(self, value):
        if isinstance(value, str):
            if value.startswith(CLAUDE_ATTRIBUTION_PREFIX):
                raise ValueError(
                    "Claude attribution must be a separate first system block."
                )
            return self.walk(value)
        if not isinstance(value, list):
            raise ValueError("Unsupported Claude system payload.")
        result: list[dict[str, Any]] = []
        for index, block in enumerate(value):
            text = block.get("text", "") if isinstance(block, dict) else ""
            if isinstance(text, str) and text.startswith(CLAUDE_ATTRIBUTION_PREFIX):
                if (
                    index != 0
                    or block.get("type") != "text"
                    or set(block) - {"type", "text", "cache_control"}
                    or not CLAUDE_ATTRIBUTION.fullmatch(text)
                ):
                    raise ValueError(
                        "Unrecognized Claude attribution; request was not forwarded."
                    )
                result.append({"type": "text", "text": text})
                if "cache_control" in block:
                    control = block["cache_control"]
                    if (
                        not isinstance(control, dict)
                        or control.get("type") != "ephemeral"
                        or set(control) - {"type", "ttl"}
                        or control.get("ttl", "5m") not in {"5m", "1h"}
                    ):
                        raise ValueError("Unrecognized attribution cache control.")
                    result[-1]["cache_control"] = deepcopy(control)
            else:
                result.append(self.walk(block))
        return result

    def response(self, body: bytes, content_type: str) -> bytes:
        if "text/event-stream" not in content_type:
            return json.dumps(
                self.walk(json.loads(body), restore=True), ensure_ascii=False
            ).encode()
        events = []
        for frame in re.split(r"\r?\n\r?\n", body.decode("utf-8")):
            # SSE line endings do not include Unicode separators inside JSON strings.
            lines = frame.replace("\r\n", "\n").split("\n")
            data = "\n".join(
                line[5:].lstrip() for line in lines if line.startswith("data:")
            )
            if not data or data == "[DONE]":
                events.append((lines, None))
            else:
                events.append((lines, json.loads(data)))
        # Fold each text/JSON stream before restoration (tokens can span any delta).
        groups: dict[tuple, list[tuple[int, dict, str]]] = {}
        thinking: dict[int, dict] = {}
        for index, (_, event) in enumerate(events):
            if not isinstance(event, dict):
                continue
            kind = event.get("type", "")
            delta = event.get("delta")
            if (
                kind == "content_block_start"
                and event.get("content_block", {}).get("type") == "thinking"
            ):
                thinking[event["index"]] = deepcopy(event["content_block"])
            if (
                kind == "content_block_delta"
                and event.get("index") in thinking
                and isinstance(delta, dict)
            ):
                block = thinking[event["index"]]
                for field in ("thinking", "signature"):
                    if field in delta:
                        block[field] = block.get(field, "") + delta[field]
            if kind == "content_block_delta" and isinstance(delta, dict):
                field = (
                    "partial_json"
                    if delta.get("type") == "input_json_delta"
                    else "text"
                )
                if field not in delta:
                    continue  # signed thinking is kept exactly as received
                target = delta
                key = (kind, event.get("index"), field)
            elif kind in {
                "response.output_text.delta",
                "response.function_call_arguments.delta",
                "response.custom_tool_call_input.delta",
            }:
                target, field = event, "delta"
                key = (
                    kind,
                    event.get("item_id"),
                    event.get("output_index"),
                    event.get("content_index"),
                )
            else:
                continue
            groups.setdefault(key, []).append((index, target, field))
        for block in thinking.values():
            self.opaque.add(self._opaque_key(block))
        for group in groups.values():
            combined = "".join(target[field] for _, target, field in group)
            field = group[0][2]
            if (
                field == "partial_json"
                or group[0][1].get("type") == "response.function_call_arguments.delta"
            ):
                restored = json.dumps(
                    self.values(json.loads(combined), restore=True), ensure_ascii=False
                )
            else:
                restored = self.restore(combined)
            for offset, (_, target, field) in enumerate(group):
                target[field] = restored if offset == 0 else ""
        output = []
        for lines, event in events:
            if event is None:
                if lines:
                    output.append("\n".join(lines) + "\n\n")
                continue
            # Deltas have already been restored; do not recursively restore twice.
            if event.get("type") not in {
                "content_block_delta",
                "response.output_text.delta",
                "response.function_call_arguments.delta",
                "response.custom_tool_call_input.delta",
            }:
                event = self.walk(event, restore=True)
            headers = [line for line in lines if not line.startswith("data:")]
            output.append(
                "\n".join(headers + ["data: " + json.dumps(event, ensure_ascii=False)])
                + "\n\n"
            )
        return "".join(output).encode()


class PrivacyGateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address, token: str, upstreams=None, state_dir: Path | None = None
    ):
        if address[0] != "127.0.0.1":
            raise ValueError("Gateway must bind to IPv4 loopback.")
        self.token = token
        self.upstreams = upstreams or UPSTREAMS
        self.sessions: dict[str, GatewaySession] = {}
        self.sessions_lock = RLock()
        self.state_dir = state_dir
        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)
            state_dir.chmod(0o700)
        super().__init__(address, GatewayHandler)

    def get_session(self, agent):
        with self.sessions_lock:
            if agent not in self.sessions:
                path = self.state_dir / f"{agent}.enc" if self.state_dir else None
                saved = (
                    load_session(path, self.token, "gateway")
                    if path and path.exists()
                    else None
                )
                session = GatewaySession(saved)
                if path and path.with_suffix(".opaque.json").exists():
                    digests = json.loads(
                        path.with_suffix(".opaque.json").read_text(encoding="utf-8")
                    )
                    if not isinstance(digests, list) or any(
                        not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{64}", x)
                        for x in digests
                    ):
                        raise ValueError("Invalid persisted reasoning state.")
                    session.opaque = set(digests)
                self.sessions[agent] = session
            return self.sessions[agent]

    def save(self, agent, session):
        session.session.touch()
        if self.state_dir:
            path = self.state_dir / f"{agent}.enc"
            save_session(path, session.session, self.token)
            _atomic_json(path.with_suffix(".opaque.json"), sorted(session.opaque))
            path.with_suffix(".opaque.json").chmod(0o600)

    def server_close(self):
        super().server_close()
        for session in self.sessions.values():
            with session.lock:
                session.session.clear()
                session.opaque.clear()
        self.sessions.clear()


class GatewayHandler(BaseHTTPRequestHandler):
    server: PrivacyGateway
    protocol_version = "HTTP/1.0"

    def log_message(self, *_):
        pass  # Never log headers, prompts, replies or tokens.

    def send_body(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in getattr(self, "response_headers", {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def capture_response_headers(self, headers):
        self.response_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() in {"retry-after", "x-should-retry"}
            or key.lower().startswith("anthropic-ratelimit-unified-")
        }

    def authorized(self):
        return secrets.compare_digest(
            self.headers.get("X-SecureMCP-Token", ""), self.server.token
        )

    def do_GET(self):
        if self.path == "/health" and self.authorized():
            self.send_body(200, b'{"service":"secure-mcp-subscription-gateway"}')
        elif self.authorized() and re.fullmatch(
            r"/codex/models(?:\?client_version=[\d.]+)?", self.path
        ):
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer eyJ") or self.headers.get("x-api-key"):
                self.send_body(401, b'{"error":"Subscription OAuth required"}')
                return
            try:
                request = urllib.request.Request(
                    self.server.upstreams["codex"] + self.path[len("/codex") :],
                    headers={
                        "Authorization": auth,
                        "chatgpt-account-id": self.headers.get(
                            "chatgpt-account-id", ""
                        ),
                    },
                )
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), NoRedirect()
                )
                with opener.open(request, timeout=30) as upstream:
                    body = upstream.read(LIMIT + 1)
                    if len(body) > LIMIT:
                        raise ValueError("Catalog size exceeded")
                    self.send_body(200, body)
            except urllib.error.HTTPError as exc:
                exc.close()
                self.send_body(
                    exc.code if exc.code in {401, 403, 429} else 502,
                    b'{"error":"Subscription catalog unavailable"}',
                )
            except (OSError, ValueError):
                self.send_body(502, b'{"error":"Subscription catalog unavailable"}')
        else:
            self.send_body(
                400 if self.authorized() else 401,
                b'{"error":"Unauthorized or unsupported endpoint"}',
            )

    def do_POST(self):
        if not self.authorized():
            self.send_body(401, b'{"error":"Unauthorized"}')
            return
        if self.path == "/shutdown":
            from threading import Thread

            self.send_body(200, b'{"stopping":true}')
            Thread(target=self.server.shutdown, daemon=True).start()
            return
        routes = {
            "/claude/v1/messages": ("claude", "/v1/messages"),
            "/claude/v1/messages/count_tokens": ("claude", "/v1/messages/count_tokens"),
            "/codex/responses": ("codex", "/responses"),
        }
        route = routes.get(self.path.split("?", 1)[0])
        if not route:
            self.send_body(
                400, b'{"error":"Unsupported endpoint; no request forwarded"}'
            )
            return
        agent, path = route
        auth = self.headers.get("Authorization", "")
        # Subscription-only boundary: never silently switch to paid API keys.
        valid_auth = (
            auth.startswith("Bearer sk-ant-oat")
            if agent == "claude"
            else auth.startswith("Bearer eyJ")
        )
        if not valid_auth or self.headers.get("x-api-key"):
            self.send_body(
                401,
                b'{"error":"Existing subscription OAuth login required; API keys refused"}',
            )
            return
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get(
                "Content-Encoding"
            ):
                raise ValueError("Encoded/chunked requests unsupported.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= LIMIT:
                raise ValueError("Request size limit exceeded.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object.")
            # Refresh stays CLI-owned; changing bearer tokens must not change aliases.
            session = self.server.get_session(agent)
            with session.lock:
                masked = session.request(payload)
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": auth,
                    "Accept": "text/event-stream"
                    if masked.get("stream")
                    else "application/json",
                }
                for key in (
                    "anthropic-version",
                    "anthropic-beta",
                    "chatgpt-account-id",
                    "OpenAI-Beta",
                    "User-Agent",
                    "originator",
                    "session_id",
                    "x-codex-beta-features",
                ):
                    if self.headers.get(key):
                        headers[key] = self.headers[key]
                request = urllib.request.Request(
                    self.server.upstreams[agent] + path,
                    data=json.dumps(masked, ensure_ascii=False).encode(),
                    headers=headers,
                )
                # Disable redirects and environment proxies: credentials stay at the fixed origin.
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}), NoRedirect()
                )
                with opener.open(request, timeout=180) as upstream:
                    self.capture_response_headers(upstream.headers)
                    body = upstream.read(LIMIT + 1)
                    if len(body) > LIMIT:
                        raise ValueError("Response size limit exceeded.")
                    content_type = upstream.headers.get(
                        "Content-Type", "application/json"
                    )
                    if masked.get("stream") and body.startswith(
                        (b"event:", b"data:", b":")
                    ):
                        content_type = "text/event-stream"
                    restored = session.response(body, content_type)
                self.server.save(agent, session)
                self.send_body(200, restored, content_type)
        except urllib.error.HTTPError as exc:
            # Preserve 401 so the CLI can refresh its own OAuth token. Never echo provider errors.
            self.capture_response_headers(exc.headers)
            exc.close()
            self.send_body(
                exc.code if exc.code in {401, 403, 429} else 502,
                b'{"error":"Subscription upstream rejected the request"}',
            )
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            self.send_body(
                400,
                b'{"error":"Privacy gateway could not safely process this request; nothing bypassed masking"}',
            )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

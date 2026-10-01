"""SecureMCP Model Context Protocol (MCP) server implementation.

Exposes tools, prompts, and resources for privacy-preserving token obfuscation
and homomorphic-style restoration.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.models import MaskMode, SurrogateStrategy
from secure_mcp.session import SessionVault

# Initialize core server instance
app = MCPServer(
    name="SecureMCP",
    version="0.1.0",
    instructions="SecureMCP protects user privacy by masking content words with synthetic surrogates while preserving grammatical and syntactic structure.",
)

# Global engine and session vault
engine = MaskingEngine()
vault = SessionVault(default_ttl=3600)


def _masking_session(session_id: str, mode: MaskMode, strategy: SurrogateStrategy):
    try:
        return vault.get_or_create(session_id, mode=mode, strategy=strategy)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@app.tool()
def mask_text(
    text: str,
    session_id: str = "default_session",
    mode: str = "content_words",
    strategy: str = "bracket",
    language: str = "auto",
) -> str:
    """Mask text by replacing content words/entities with synthetic surrogates while preserving grammar and syntax.

    Args:
        text: The input text to be masked before sending to the LLM.
        session_id: Session identifier to isolate token mapping tables.
        mode: Masking policy ('content_words', 'entities_only', 'code_aware', 'aggressive').
        strategy: Surrogate format ('bracket', 'unicode', 'pseudoword', 'hash').
        language: Language code ('auto', 'en', 'ko').

    Returns:
        JSON string containing the masked text, session ID, and privacy metrics.
    """
    try:
        mask_mode = MaskMode(mode)
    except ValueError:
        mask_mode = MaskMode.CONTENT_WORDS

    try:
        strat = SurrogateStrategy(strategy)
    except ValueError:
        strat = SurrogateStrategy.BRACKET

    session = _masking_session(session_id, mask_mode, strat)
    session.total_mask_calls += 1

    result = engine.mask_text(
        text=text,
        session_id=session.session_id,
        generator=session.generator,
        mapping_store=session.forward_store,
        reverse_store=session.reverse_store,
        mode=mask_mode,
        strategy=strat,
        language=language,
    )
    return result.model_dump_json(indent=2)


@app.tool()
def unmask_text(
    masked_text: str,
    session_id: str = "default_session",
    strategy: Optional[str] = None,
) -> str:
    """Restore original tokens from an AI-generated response containing synthetic surrogates.

    Args:
        masked_text: The AI output containing synthetic surrogate tokens (e.g. [ENT_1], ⟦NOUN_2⟧).
        session_id: Session identifier matching the mask_text call.
        strategy: Optional strategy; defaults to the session's strategy.

    Returns:
        JSON string containing the restored unmasked text and token metrics.
    """
    session = vault.get_session(session_id)
    if not session:
        return json.dumps({
            "error": f"Session '{session_id}' not found or expired.",
            "unmasked_text": masked_text,
            "restored_tokens_count": 0,
        }, indent=2)

    try:
        strat = SurrogateStrategy(strategy) if strategy is not None else session.strategy
    except ValueError:
        strat = session.strategy

    if strat != session.strategy:
        raise ToolError("Unmask strategy must match the session's strategy.")

    session.total_unmask_calls += 1
    result = engine.unmask(
        masked_text=masked_text,
        session_id=session.session_id,
        reverse_store=session.reverse_store,
        strategy=strat,
    )
    return result.model_dump_json(indent=2)


@app.tool()
def mask_code(
    code: str,
    session_id: str = "default_session",
    strategy: str = "bracket",
) -> str:
    """Mask code identifiers and string literals while keeping language keywords, operators, and control flow intact.

    Args:
        code: The source code to obfuscate (Python, JavaScript, TypeScript, Go, Rust, Java, C++, SQL).
        session_id: Session identifier to isolate token mapping tables.
        strategy: Surrogate format ('bracket', 'unicode', 'pseudoword', 'hash').

    Returns:
        JSON string containing the masked code and privacy metrics.
    """
    try:
        strat = SurrogateStrategy(strategy)
    except ValueError:
        strat = SurrogateStrategy.BRACKET

    session = _masking_session(session_id, MaskMode.CODE_AWARE, strat)
    session.total_mask_calls += 1

    result = engine.mask_code(
        code=code,
        session_id=session.session_id,
        generator=session.generator,
        mapping_store=session.forward_store,
        reverse_store=session.reverse_store,
        strategy=strat,
    )
    return result.model_dump_json(indent=2)


@app.tool()
def unmask_code(
    masked_code: str,
    session_id: str = "default_session",
    strategy: Optional[str] = None,
) -> str:
    """Restore original identifiers and string literals in code returned by the LLM.

    Args:
        masked_code: The obfuscated code returned by the AI.
        session_id: Session identifier matching the mask_code call.
        strategy: Optional strategy; defaults to the session's strategy.

    Returns:
        JSON string containing the restored code and restoration metrics.
    """
    session = vault.get_session(session_id)
    if not session:
        return json.dumps({
            "error": f"Session '{session_id}' not found or expired.",
            "unmasked_code": masked_code,
            "restored_tokens_count": 0,
        }, indent=2)

    try:
        strat = SurrogateStrategy(strategy) if strategy is not None else session.strategy
    except ValueError:
        strat = session.strategy

    if strat != session.strategy:
        raise ToolError("Unmask strategy must match the session's strategy.")

    session.total_unmask_calls += 1
    result = engine.unmask(
        masked_text=masked_code,
        session_id=session.session_id,
        reverse_store=session.reverse_store,
        strategy=strat,
    )
    return result.model_dump_json(indent=2)


@app.tool()
def create_privacy_session(
    session_id: str = "",
    mode: str = "content_words",
    strategy: str = "bracket",
    ttl_seconds: int = 3600,
) -> str:
    """Create a new isolated privacy session with explicit security parameters.

    Args:
        session_id: Optional custom session ID. If empty, a random UUID will be generated.
        mode: Masking policy ('content_words', 'entities_only', 'code_aware', 'aggressive').
        strategy: Surrogate format ('bracket', 'unicode', 'pseudoword', 'hash').
        ttl_seconds: Time-To-Live in seconds before session tables are automatically wiped.

    Returns:
        JSON string containing the session confirmation and metadata.
    """
    sid = session_id.strip() if session_id.strip() else f"sess_{uuid.uuid4().hex[:12]}"
    try:
        mask_mode = MaskMode(mode)
    except ValueError:
        mask_mode = MaskMode.CONTENT_WORDS

    try:
        strat = SurrogateStrategy(strategy)
    except ValueError:
        strat = SurrogateStrategy.BRACKET

    session = _masking_session(sid, mask_mode, strat)
    session.ttl_seconds = ttl_seconds

    return json.dumps({
        "status": "created",
        "session_id": session.session_id,
        "mode": session.mode.value,
        "strategy": session.strategy.value,
        "ttl_seconds": session.ttl_seconds,
    }, indent=2)


@app.tool()
def get_session_stats(session_id: str = "default_session") -> str:
    """Retrieve privacy statistics for a session without disclosing raw sensitive tokens.

    Args:
        session_id: Session identifier.

    Returns:
        JSON string containing masked token counts, invocation counts, and category distributions.
    """
    session = vault.get_session(session_id)
    if not session:
        return json.dumps({"error": f"Session '{session_id}' not found."}, indent=2)

    return session.get_stats().model_dump_json(indent=2)


@app.tool()
def clear_privacy_session(session_id: str = "default_session") -> str:
    """Securely purge all substitution tables and memory mappings for a session.

    Args:
        session_id: Session identifier to purge.

    Returns:
        JSON string confirming purge status.
    """
    cleared = vault.clear_session(session_id)
    return json.dumps({
        "session_id": session_id,
        "purged": cleared,
        "status": "zero-trace memory wipe complete" if cleared else "session not found",
    }, indent=2)


# ============================================================================
# Prompts
# ============================================================================

@app.prompt()
def privacy_preserving_assistant(task_description: str = "") -> str:
    """System instruction instructing an LLM on how to reason over masked surrogate tokens."""
    return f"""You are operating as a privacy-preserving assistant.
The user's input text has been pre-processed by SecureMCP to protect confidential data, proprietary entities, and PII.

Guidelines:
1. Synthetic surrogate placeholders (e.g. `[ENT_1]`, `[NOUN_2]`, `[NUM_1]`, or `⟦ENT_1⟧`) represent real entities whose identities have been concealed client-side.
2. Maintain complete grammatical fidelity and preserve the exact placeholder tokens in your reasoning and generated response.
3. Do NOT modify the bracket syntax or hallucinate real-world identities for placeholder tokens.
4. The client's local SecureMCP server will safely restore all placeholders into original content upon receiving your response.

Task: {task_description or "Process the user query using the provided context."}
"""


@app.prompt()
def secure_code_assistant(goal: str = "refactor") -> str:
    """System prompt instructing an LLM to review or refactor code with obfuscated identifiers."""
    return f"""You are reviewing or refactoring code that has been obfuscated by SecureMCP.
All variable names, function names, and sensitive string literals have been replaced with synthetic identifiers (e.g., `[ID_1]`, `[LIT_1]`).

Rules:
1. Preserve all placeholder tokens (`[ID_1]`, `[LIT_1]`, etc.) exactly as written.
2. Focus on code structure, algorithmic efficiency, bug detection, type safety, and clean architecture.
3. Goal: {goal}.
"""


# ============================================================================
# Resources
# ============================================================================

@app.resource("privacy://policies")
def get_privacy_policies() -> str:
    """Documentation of supported masking modes and surrogate strategies."""
    policies = {
        "modes": {
            "content_words": "Preserves grammar, syntax, prepositions, conjunctions, pronouns; masks content nouns, entities, numbers.",
            "entities_only": "Masks only detected entities (names, emails, phone numbers, IPs, secrets, numbers).",
            "code_aware": "Preserves programming language keywords, operators, and control flow; masks identifiers and string literals.",
            "aggressive": "Masks all tokens except structural closed-class functional words and punctuation.",
        },
        "strategies": {
            "bracket": "Distinct explicit tags e.g. [ENT_1], [NOUN_2], [NUM_1]",
            "unicode": "Mathematical enclosure brackets e.g. ⟦ENT_1⟧, resisting BPE sub-token splits",
            "pseudoword": "Pronounceable natural nonce words e.g. Brivon, Cranley, keeping natural LLM perplexity",
            "hash": "Salted cryptographic nonces e.g. ~h1_8f3a~",
        },
        "languages": ["en", "ko", "auto"],
    }
    return json.dumps(policies, indent=2)


@app.resource("privacy://status")
def get_privacy_status() -> str:
    """Live status of SecureMCP server and active sessions count."""
    return json.dumps({
        "server": "SecureMCP",
        "version": "0.1.0",
        "active_sessions": len(vault._sessions),
        "protocol": "Model Context Protocol (MCP)",
        "security_standard": "Zero-Knowledge Local Substitution",
    }, indent=2)

"""Command-Line Interface (CLI) for SecureMCP."""

from __future__ import annotations

import json
import sys
import time
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from secure_mcp.server import app, engine, vault
from secure_mcp.models import MaskMode, SurrogateStrategy

# Ensure UTF-8 output on Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(legacy_windows=False)


@click.group()
@click.version_option(version="0.1.0")
def main():
    """SecureMCP: Grammar-Preserving Zero-Knowledge Semantic Masking & Anonymization for LLMs."""
    pass


@main.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"], case_sensitive=False),
    default="stdio",
    help="MCP transport protocol (stdio for local clients like Claude Desktop/Cursor, sse for remote).",
)
@click.option("--host", default="127.0.0.1", help="Host to bind for HTTP/SSE transport.")
@click.option("--port", default=8000, type=int, help="Port to bind for HTTP/SSE transport.")
def serve(transport: str, host: str, port: int):
    """Start the SecureMCP server to listen for tool calls from Claude Desktop, Cursor, or AI agents."""
    transport_lower = transport.lower()
    if transport_lower != "stdio":
        console.print(f"[bold green]Starting SecureMCP server on {host}:{port} via {transport_lower}...[/bold green]")
        app.run(transport=transport_lower, host=host, port=port)
    else:
        # stdio runs silently so as not to corrupt JSON-RPC protocol messages
        app.run(transport="stdio")


@main.command()
@click.argument("text")
@click.option("--session-id", default="cli_session", help="Session ID for mapping isolation.")
@click.option(
    "--mode",
    type=click.Choice(["content_words", "entities_only", "code_aware", "aggressive"]),
    default="content_words",
    help="Masking policy.",
)
@click.option(
    "--strategy",
    type=click.Choice(["bracket", "unicode", "pseudoword", "hash"]),
    default="bracket",
    help="Surrogate token strategy.",
)
@click.option("--language", default="auto", help="Language ('auto', 'en', 'ko').")
def mask(text: str, session_id: str, mode: str, strategy: str, language: str):
    """Mask text using grammar-preserving token obfuscation."""
    strat = SurrogateStrategy(strategy)
    mask_mode = MaskMode(mode)
    session = vault.get_or_create(session_id, mode=mask_mode, strategy=strat)

    res = engine.mask_text(
        text=text,
        session_id=session.session_id,
        generator=session.generator,
        mapping_store=session.forward_store,
        reverse_store=session.reverse_store,
        mode=mask_mode,
        strategy=strat,
        language=language,
    )

    console.print(Panel(text, title="[cyan]Original Input[/cyan]", border_style="blue"))
    console.print(Panel(res.masked_text, title="[green]Masked Output (Safe for LLM)[/green]", border_style="green"))

    table = Table(title="Privacy & Token Metrics", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="yellow")
    table.add_row("Session ID", res.session_id)
    table.add_row("Detected Language", res.detected_language)
    table.add_row("Total Tokens", str(res.total_tokens))
    table.add_row("Masked Content Tokens", str(res.masked_tokens))
    table.add_row("Preserved Grammar Tokens", str(res.preserved_tokens))
    table.add_row("Privacy Obfuscation Score", f"{res.privacy_entropy_score * 100:.1f}%")
    console.print(table)


@main.command()
@click.argument("masked_text")
@click.option("--session-id", default="cli_session", help="Session ID matching the mask operation.")
@click.option(
    "--strategy",
    type=click.Choice(["bracket", "unicode", "pseudoword", "hash"]),
    default="bracket",
    help="Surrogate token strategy.",
)
def unmask(masked_text: str, session_id: str, strategy: str):
    """Restore original tokens from an AI-generated response."""
    session = vault.get_session(session_id)
    if not session:
        console.print(f"[bold red]Error: Session '{session_id}' not found.[/bold red]")
        sys.exit(1)

    strat = SurrogateStrategy(strategy)
    res = engine.unmask(
        masked_text=masked_text,
        session_id=session.session_id,
        reverse_store=session.reverse_store,
        strategy=strat,
    )

    console.print(Panel(masked_text, title="[yellow]Masked AI Output[/yellow]", border_style="yellow"))
    console.print(Panel(res.unmasked_text, title="[bold green]Restored Original Text[/bold green]", border_style="green"))
    console.print(f"[bold cyan]Restored Tokens:[/bold cyan] {res.restored_tokens_count}")


@main.command()
def demo():
    """Run an interactive demonstration of English, Korean, and Code semantic masking."""
    console.print(Panel.fit(
        "[bold cyan]SecureMCP Demonstration[/bold cyan]\n"
        "Zero-Knowledge Grammar-Preserving Semantic Masking for OpenAI & Claude",
        border_style="cyan"
    ))

    # Demo 1: English M&A / Corporate
    en_input = "Yesterday, Pfizer announced a $43,000,000 acquisition of Seagen to accelerate oncology drug development."
    session_en = vault.get_or_create("demo_en", mode=MaskMode.CONTENT_WORDS, strategy=SurrogateStrategy.BRACKET)
    res_en = engine.mask_text(
        en_input, "demo_en", session_en.generator, session_en.forward_store, session_en.reverse_store
    )

    console.print("\n[bold yellow]=== 1. English Enterprise Data Masking ===[/bold yellow]")
    console.print(f"[bold]Original:[/bold] {en_input}")
    console.print(f"[bold green]Masked to LLM:[/bold green] {res_en.masked_text}")

    # Simulated LLM response
    sim_ai_en = f"The strategic acquisition of [ENT_2] by [ENT_1] for [NUM_1] enhances their capabilities in [NOUN_2] [NOUN_3]."
    console.print(f"[bold blue]Simulated LLM Response:[/bold blue] {sim_ai_en}")

    restored_en = engine.unmask(sim_ai_en, "demo_en", session_en.reverse_store)
    console.print(f"[bold magenta]Restored on Client:[/bold magenta] {restored_en.unmasked_text}")

    # Demo 2: Korean Agglutinative Preservation
    ko_input = "삼성전자가 카카오와 협력하여 차세대 보안 AI 플랫폼을 개발하기로 계약을 체결했습니다."
    session_ko = vault.get_or_create("demo_ko", mode=MaskMode.CONTENT_WORDS, strategy=SurrogateStrategy.UNICODE)
    res_ko = engine.mask_text(
        ko_input, "demo_ko", session_ko.generator, session_ko.forward_store, session_ko.reverse_store,
        strategy=SurrogateStrategy.UNICODE, language="ko"
    )

    console.print("\n[bold yellow]=== 2. Korean Grammar (조사/어미) Preservation Masking ===[/bold yellow]")
    console.print(f"[bold]Original:[/bold] {ko_input}")
    console.print(f"[bold green]Masked to LLM:[/bold green] {res_ko.masked_text}")

    sim_ai_ko = f"⟦ENT_1⟧와 ⟦ENT_2⟧의 협력 체결은 ⟦NOUN_2⟧ 분야에서 혁신적인 성과를 낼 것으로 기대됩니다."
    console.print(f"[bold blue]Simulated LLM Response:[/bold blue] {sim_ai_ko}")

    restored_ko = engine.unmask(sim_ai_ko, "demo_ko", session_ko.reverse_store, strategy=SurrogateStrategy.UNICODE)
    console.print(f"[bold magenta]Restored on Client:[/bold magenta] {restored_ko.unmasked_text}")

    # Demo 3: Code Obfuscation
    code_input = """def calculate_credit_score(user_account, transaction_history):
    base_rating = 750
    if len(transaction_history) > 10:
        return base_rating + 50
    return base_rating"""
    session_code = vault.get_or_create("demo_code", mode=MaskMode.CODE_AWARE, strategy=SurrogateStrategy.BRACKET)
    res_code = engine.mask_code(
        code_input, "demo_code", session_code.generator, session_code.forward_store, session_code.reverse_code_store if hasattr(session_code, 'reverse_code_store') else session_code.reverse_store
    )

    console.print("\n[bold yellow]=== 3. Proprietary Source Code Obfuscation ===[/bold yellow]")
    console.print(Panel(code_input, title="Raw Code", border_style="red"))
    console.print(Panel(res_code.masked_text, title="Masked Code (Keywords & Syntax Preserved)", border_style="green"))

    restored_code = engine.unmask(res_code.masked_text, "demo_code", session_code.reverse_store)
    console.print(f"[bold green]Restoration Verification:[/bold green] {'100% Identical' if restored_code.unmasked_text == code_input else 'Mismatch'}")


@main.command()
def benchmark():
    """Run performance throughput and entropy verification benchmarks."""
    console.print("[bold cyan]Running SecureMCP Performance & Privacy Benchmark...[/bold cyan]\n")

    sample_text = (
        "In modern enterprise cloud architectures, Snowflake database instances process confidential financial records. "
        "Engineers at Microsoft collaborate with OpenAI researchers to optimize transformer attention layers without exposing "
        "proprietary patient health information or banking credentials."
    ) * 50  # ~2,000 words

    session = vault.get_or_create("bench_session")

    # Masking Benchmark
    start_t = time.perf_counter()
    res = engine.mask_text(
        sample_text, "bench_session", session.generator, session.forward_store, session.reverse_store
    )
    mask_duration = time.perf_counter() - start_t

    # Unmasking Benchmark
    start_t = time.perf_counter()
    unmask_res = engine.unmask(res.masked_text, "bench_session", session.reverse_store)
    unmask_duration = time.perf_counter() - start_t

    words_processed = len(sample_text.split())
    mask_throughput = words_processed / max(0.0001, mask_duration)
    unmask_throughput = words_processed / max(0.0001, unmask_duration)

    table = Table(title="Benchmark Results", show_header=True)
    table.add_column("Benchmark Metric", style="bold")
    table.add_column("Value", style="green")

    table.add_row("Total Words Processed", f"{words_processed:,} words")
    table.add_row("Masking Time", f"{mask_duration * 1000:.2f} ms")
    table.add_row("Masking Speed", f"{mask_throughput:,.0f} words/sec")
    table.add_row("Unmasking Time", f"{unmask_duration * 1000:.2f} ms")
    table.add_row("Unmasking Speed", f"{unmask_throughput:,.0f} words/sec")
    table.add_row("Privacy Obfuscation Ratio", f"{res.privacy_entropy_score * 100:.1f}%")
    table.add_row("Roundtrip Restoration Accuracy", "100.0% (Zero Divergence)" if unmask_res.unmasked_text == sample_text else "Discrepancy Detected")

    console.print(table)


if __name__ == "__main__":
    main()

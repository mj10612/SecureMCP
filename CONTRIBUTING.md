# Contributing to SecureMCP

Thank you for your interest in contributing to **SecureMCP**! We welcome contributions from developers, security researchers, and linguists worldwide.

---

## Code of Conduct

We are committed to providing a welcoming and inclusive environment for everyone. Please be respectful and constructive in all discussions, issues, and pull requests.

---

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/mj10612/SecureMCP.git
   cd SecureMCP
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   # Using uv (recommended)
   uv venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   uv pip install -e ".[dev]"
   ```

3. **Run the test suite:**
   ```bash
   python -m ruff check src tests examples
   python -m mypy src
   python -m pytest -W error --cov=secure_mcp --cov-report=term-missing --cov-fail-under=85
   ```

4. **Run the demo and benchmark:**
   ```bash
   python -m secure_mcp demo
   python -m secure_mcp benchmark
   ```

The default suite uses synthetic fixtures. Optional native-host fixtures require
`SECURE_MCP_CLAUDE_BINARY`, `SECURE_MCP_CODEX_BINARY`, or
`SECURE_MCP_GROK_BINARY` pointing to the relevant executable; these tests use
isolated homes and local mock providers, without real provider inference.
Antigravity configuration tests use `SECURE_MCP_AGY_BINARY` or `agy` on `PATH`.

Live subscription tests require the explicit `SECURE_MCP_LIVE_SUBSCRIPTION=1`
opt-in plus the relevant configured CLI and login. They call real services and
can consume subscription quota. Leave that variable unset for ordinary local
checks. The Antigravity live MCP handshake has the same opt-in requirement.

---

## Pull Request Guidelines

1. Ensure all existing tests pass and write new unit tests for any added features or bug fixes.
2. Maintain clean type annotations and follow PEP 8 standards.
3. Keep originals and mapping tables in the trusted local host. Restore for local display, outside the provider/model context.

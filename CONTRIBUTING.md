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

---

## Pull Request Guidelines

1. Ensure all existing tests pass and write new unit tests for any added features or bug fixes.
2. Maintain clean type annotations and follow PEP 8 standards.
3. Keep originals and mapping tables in the trusted local host. Restore for local display, outside the provider/model context.

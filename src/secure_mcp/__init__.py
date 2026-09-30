"""SecureMCP: Grammar-Preserving Zero-Knowledge Semantic Masking & Anonymization MCP Server.

Enables secure privacy-preserving inference with OpenAI, Claude, and other LLMs by
transforming sensitive content tokens into meaningless synthetic surrogates while keeping
the grammatical, relational, and syntactic backbone completely intact.
"""

__version__ = "0.1.0"
__author__ = "SecureMCP Contributors"
__license__ = "Apache-2.0"

from secure_mcp.models import (
    MaskMode,
    SurrogateStrategy,
    TokenType,
    TokenMapping,
    MaskResult,
    UnmaskResult,
    SessionStats,
)
from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.session import SessionVault

__all__ = [
    "MaskMode",
    "SurrogateStrategy",
    "TokenType",
    "TokenMapping",
    "MaskResult",
    "UnmaskResult",
    "SessionStats",
    "MaskingEngine",
    "SessionVault",
]

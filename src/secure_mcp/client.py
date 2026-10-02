"""A trusted host boundary: the provider receives only the masked payload."""

from __future__ import annotations

from typing import Callable
import uuid

from secure_mcp.engine.masking_engine import MaskingEngine
from secure_mcp.models import MaskMode, SurrogateStrategy, UnmaskResult
from secure_mcp.session import SessionVault


class LocalPrivacyClient:
    """Keep this object and the restored response outside the provider/model context."""

    def __init__(self) -> None:
        self.vault = SessionVault()
        self.engine = MaskingEngine()

    def request(
        self,
        text: str,
        provider: Callable[[str], str],
        mode: MaskMode = MaskMode.CONTENT_WORDS,
        strategy: SurrogateStrategy = SurrogateStrategy.BRACKET,
        sensitive_terms: set[str] | None = None,
        language: str = "auto",
        normalize_particles: bool = False,
    ) -> UnmaskResult:
        session = self.vault.create(f"sess_{uuid.uuid4().hex}", mode, strategy)
        try:
            masked = self.engine.mask_text(
                text,
                session.session_id,
                session.generator,
                session.forward_store,
                session.reverse_store,
                mode=mode,
                sensitive_terms=sensitive_terms,
                language=language,
            )
            response = provider(masked.masked_text)
            return self.engine.unmask(
                response,
                session.session_id,
                session.reverse_store,
                strategy,
                normalize_particles=normalize_particles,
                strict=True,
            )
        finally:
            self.vault.clear_session(session.session_id)

    def close(self) -> None:
        self.vault.close()

    def __enter__(self) -> LocalPrivacyClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

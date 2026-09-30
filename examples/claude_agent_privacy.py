"""Example demonstrating privacy-preserving Anthropic Claude API interaction with SecureMCP."""

import os
from secure_mcp import MaskingEngine, SessionVault, MaskMode, SurrogateStrategy

# In a real environment, you would use:
# import anthropic
# client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def run_claude_privacy_pipeline():
    vault = SessionVault()
    engine = MaskingEngine()
    session = vault.get_or_create("claude_session", mode=MaskMode.CONTENT_WORDS, strategy=SurrogateStrategy.UNICODE)

    korean_text = "네이버 클라우드가 카카오페이와 연계하여 150억원의 신규 결제 보안 인프라를 구축하기로 합의했습니다."
    print("=" * 60)
    print("1. KOREAN ORIGINAL TEXT:")
    print(korean_text)

    mask_res = engine.mask_text(
        text=korean_text,
        session_id=session.session_id,
        generator=session.generator,
        mapping_store=session.forward_store,
        reverse_store=session.reverse_store,
        strategy=SurrogateStrategy.UNICODE,
        language="ko"
    )
    print("\n2. OBFUSCATED PAYLOAD SENT TO CLAUDE (Syntax/조사 Preserved):")
    print(mask_res.masked_text)

    # Simulated Claude response
    claude_reply = "⟦ENT_1⟧와 ⟦ENT_2⟧ 간의 ⟦NOUN_1⟧ 구축 합의는 ⟦NUM_1⟧ 규모의 대형 ⟦NOUN_2⟧ 프로젝트로 평가됩니다."
    print("\n3. CLAUDE RESPONSE WITH UNICODE SURROGATES:")
    print(claude_reply)

    restored = engine.unmask(claude_reply, session.session_id, session.reverse_store, strategy=SurrogateStrategy.UNICODE)
    print("\n4. RESTORED RESPONSE ON CLIENT:")
    print(restored.unmasked_text)
    print("=" * 60)

if __name__ == "__main__":
    run_claude_privacy_pipeline()

"""Example demonstrating privacy-preserving OpenAI API interaction via SecureMCP."""

import os
from secure_mcp import MaskingEngine, SessionVault, MaskMode, SurrogateStrategy

# In a real environment, you would use:
# from openai import OpenAI
# client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def run_privacy_pipeline():
    # 1. Initialize local privacy session
    vault = SessionVault()
    engine = MaskingEngine()
    session = vault.get_or_create("openai_session", mode=MaskMode.CONTENT_WORDS, strategy=SurrogateStrategy.BRACKET)

    # 2. Raw confidential prompt with patient PII and medical data
    raw_prompt = (
        "Patient John Doe (SSN: 000-12-3456) was admitted to St. Jude Children's Research Hospital "
        "with severe acute lymphoblastic leukemia and prescribed 50mg of Dasatinib daily."
    )
    print("=" * 60)
    print("1. RAW PROMPT (Never leaves local machine):")
    print(raw_prompt)

    # 3. Mask prompt client-side before transmitting over network
    mask_res = engine.mask_text(
        text=raw_prompt,
        session_id=session.session_id,
        generator=session.generator,
        mapping_store=session.forward_store,
        reverse_store=session.reverse_store,
    )
    print("\n2. MASKED PAYLOAD TRANSMITTED TO OPENAI (Zero PII / Zero Content Leakage):")
    print(mask_res.masked_text)
    print(f"Privacy Obfuscation Ratio: {mask_res.privacy_entropy_score * 100:.1f}%")

    # 4. Simulated response from OpenAI (gpt-4o) reasoning over the grammar & placeholders:
    # response = client.chat.completions.create(
    #     model="gpt-4o",
    #     messages=[
    #         {"role": "system", "content": "You are a medical reasoning assistant. Maintain all placeholder tokens like [ENT_1], [NOUN_2], [NUM_1] strictly unchanged."},
    #         {"role": "user", "content": f"Summarize the treatment plan for: {mask_res.masked_text}"}
    #     ]
    # )
    simulated_openai_response = (
        "Treatment Summary: [NOUN_1] [ENT_1] is undergoing treatment for [NOUN_5] [NOUN_6] [NOUN_7] "
        "at [ENT_2]. The recommended dosage is [NUM_2] [NOUN_8] administered on a daily basis."
    )
    print("\n3. RESPONSE RECEIVED FROM OPENAI (Containing synthetic placeholders):")
    print(simulated_openai_response)

    # 5. Restore original text locally on client machine
    unmask_res = engine.unmask(
        masked_text=simulated_openai_response,
        session_id=session.session_id,
        reverse_store=session.reverse_store,
    )
    print("\n4. LOCALLY RESTORED CLINICAL REPORT (Zero data leaked to cloud):")
    print(unmask_res.unmasked_text)
    print("=" * 60)

if __name__ == "__main__":
    run_privacy_pipeline()

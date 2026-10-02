"""English trusted-host pipeline demo. No network calls or API keys required."""

import sys

from secure_mcp import LocalPrivacyClient


def run_privacy_pipeline():
    text = (
        "Patient John Doe (SSN: 000-12-3456) was admitted to St. Jude Children's Research Hospital "
        "with acute lymphoblastic leukemia and prescribed 50mg of Dasatinib daily."
    )

    def simulated_provider(masked_payload):
        # Replace with your provider call; send ONLY this argument.
        print("MASKED:", masked_payload)
        return (
            masked_payload  # Mock echo verifies exact restoration, not model reasoning.
        )

    with LocalPrivacyClient() as client:
        result = client.request(
            text,
            simulated_provider,
            sensitive_terms={"John Doe", "St. Jude Children's Research Hospital"},
        )
        assert result.unmasked_text == text
        print("RESTORED:", result.unmasked_text)
        return result.unmasked_text


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run_privacy_pipeline()

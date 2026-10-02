"""Korean trusted-host pipeline demo. The callback receives only the masked text."""

import sys

from secure_mcp import LocalPrivacyClient, SurrogateStrategy


def run_claude_privacy_pipeline():
    text = "네이버 클라우드가 카카오페이와 연계하여 150억원의 신규 결제 보안 인프라를 구축하기로 합의했습니다."

    def simulated_provider(masked_payload):
        print("MASKED:", masked_payload)
        return masked_payload

    with LocalPrivacyClient() as client:
        result = client.request(
            text,
            simulated_provider,
            strategy=SurrogateStrategy.UNICODE,
            language="ko",
            sensitive_terms={"네이버", "카카오페이"},
        )
        assert result.unmasked_text == text
        print("RESTORED:", result.unmasked_text)
        return result.unmasked_text


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run_claude_privacy_pipeline()

"""Native Anthropic context errors remain actionable without revealing details."""

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from secure_mcp.gateway import sanitized_upstream_error


@pytest.mark.parametrize("message", ["prompt is too long: 210000 tokens > 200000 maximum alice@example.com", "Prompt is too long", "PROMPT IS TOO LONG; privateFile"])
def test_anthropic_context_error_without_code_is_classified(message):
    upstream = HTTPError("https://provider", 400, "bad request", {}, BytesIO(json.dumps({"error": {"type": "invalid_request_error", "message": message}}).encode()))
    status, body = sanitized_upstream_error(upstream)
    error = json.loads(body)["error"]
    assert status == 400
    assert error["code"] == "context_length_exceeded"
    assert "prompt is too long" in error["message"].lower()
    assert "alice@example.com" not in str(error)
    assert "210000" not in str(error)
    assert "privateFile" not in str(error)


@pytest.mark.parametrize("message", ["Echoed secret says prompt is too long: private", "prompt is too longish: private", "private password"])
def test_unknown_upstream_messages_are_not_forwarded_or_classified(message):
    upstream = HTTPError("https://provider", 400, "bad request", {}, BytesIO(json.dumps({"error": {"type": "invalid_request_error", "message": message}}).encode()))
    _, body = sanitized_upstream_error(upstream)
    error = json.loads(body)["error"]
    assert "code" not in error
    assert error["message"] == "Upstream rejected the request."

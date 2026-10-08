"""Bounded, authenticated xAI catalog fetch with public metadata normalization."""

import json
import re
import urllib.parse
import urllib.request


def fetch_catalog(origin: str, auth: str) -> bytes:
    """Fetch only ``/v1/models`` from the fixed provider or a loopback fixture."""
    # Imported at call time so GatewayHandler can import this small helper.
    from secure_mcp.gateway import API_UPSTREAMS, LIMIT, NoRedirect

    if origin != API_UPSTREAMS["xai"]:
        if not isinstance(origin, str) or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):
            raise ValueError("Catalog requires a fixed xAI origin.")
        port = urllib.parse.urlsplit(origin).port
        if port is None or not 1 <= port <= 65535:
            raise ValueError("Invalid catalog fixture port.")
    if (
        not isinstance(auth, str)
        or not auth.startswith("Bearer xai-")
        or not 12 <= len(auth) <= 4096
        or any(ord(char) < 32 or ord(char) > 126 for char in auth)
    ):
        raise ValueError("Catalog requires explicit xAI API authentication.")
    request = urllib.request.Request(origin + "/v1/models", headers={"Authorization": auth, "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=30) as upstream:
        body = upstream.read(LIMIT + 1)
        if len(body) > LIMIT:
            raise ValueError("Catalog size limit exceeded.")
        if upstream.headers.get_content_type() != "application/json":
            raise ValueError("Catalog must be JSON.")
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid model catalog.")
    models = []
    for item in payload["data"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", item["id"])
        ):
            raise ValueError("Invalid public model identifier.")
        model = {"id": item["id"], "object": "model"}
        if type(item.get("created")) is int and 0 <= item["created"] <= 253402300799:
            model["created"] = item["created"]
        if isinstance(item.get("owned_by"), str) and item["owned_by"] in {"xai", "x.ai"}:
            model["owned_by"] = item["owned_by"]
        models.append(model)
    return json.dumps({"object": "list", "data": models}).encode()

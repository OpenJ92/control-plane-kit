"""Controlled signed-webhook receiver used only by live acceptance."""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request


app = FastAPI(title="control-plane-kit webhook receiver fixture")
_received: dict[str, dict[str, object]] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/hook")
async def receive(
    request: Request,
    x_cpk_webhook_delivery: str | None = Header(default=None),
    x_cpk_webhook_signature: str | None = Header(default=None),
):
    body = await request.body()
    if not x_cpk_webhook_delivery or len(body) > 1_048_576:
        raise HTTPException(status_code=400, detail="invalid fixture request")
    expected = "sha256=" + hmac.new(
        os.environ["CPK_WEBHOOK_RECEIVER_SECRET"].encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not x_cpk_webhook_signature or not hmac.compare_digest(
        x_cpk_webhook_signature,
        expected,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")
    _received[x_cpk_webhook_delivery] = {
        "body": body.decode("utf-8"),
        "signature_valid": True,
    }
    return {"accepted": True}


@app.get("/received/{delivery_id}")
def received(delivery_id: str) -> dict[str, object]:
    value = _received.get(delivery_id)
    if value is None:
        raise HTTPException(status_code=404, detail="not received")
    return value


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="warning")


if __name__ == "__main__":
    main()

"""
FastAPI webhook receiver for SMS forwarded from an Android device.

Endpoints:
  POST /webhook/sms       — receive a new bank SMS
  POST /webhook/heartbeat — keep-alive ping from the SMS forwarder app
"""

import logging
import os
import time
from typing import Optional

import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="SMS Webhook Receiver", docs_url=None, redoc_url=None)

_redis_client: Optional[aioredis.Redis] = None
SMS_QUEUE_KEY = "sms_queue"
HEARTBEAT_KEY = "sms_forwarder_heartbeat"


# ---------------------------------------------------------------------------
# Redis lifecycle
# ---------------------------------------------------------------------------

def get_redis() -> aioredis.Redis:
    """Return the shared Redis client; raises if not initialised."""
    if _redis_client is None:
        raise RuntimeError("Redis client not initialised.")
    return _redis_client


async def init_redis(redis_url: str) -> None:
    """Create the Redis client used by the webhook."""
    global _redis_client
    _redis_client = aioredis.from_url(redis_url, decode_responses=True)
    logger.info("Webhook Redis client initialised.")


async def close_redis() -> None:
    """Close the Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Webhook Redis client closed.")


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class SMSPayload(BaseModel):
    """Payload sent by the Android SMS forwarder app."""
    # The sender ID, e.g. "HDFCBK"
    sender: Optional[str] = None
    # Alias for apps that use "from" as the key
    body: str
    timestamp: Optional[str] = None

    class Config:
        # Allow "from" as an alias so JSON {"from": "...", "body": "..."} works
        populate_by_name = True


class HeartbeatPayload(BaseModel):
    """Optional heartbeat payload."""
    device_id: Optional[str] = None
    timestamp: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _verify_secret(x_sms_secret: Optional[str]) -> None:
    expected = os.environ.get("SMS_WEBHOOK_SECRET", "")
    if not expected:
        logger.warning("SMS_WEBHOOK_SECRET is not set — webhook is unprotected!")
        return
    if x_sms_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-SMS-Secret header.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/webhook/sms", status_code=status.HTTP_202_ACCEPTED)
async def receive_sms(
    request: Request,
    x_sms_secret: Optional[str] = Header(default=None, alias="X-SMS-Secret"),
) -> dict:
    """
    Receive a bank credit SMS from the Android forwarder.

    The forwarder app may use different JSON keys depending on the app used.
    We parse the raw body to handle both ``body`` and ``from``/``message`` keys.
    """
    _verify_secret(x_sms_secret)

    raw = await request.json()
    # Support multiple field name conventions used by different forwarder apps
    sms_body: str = (
        raw.get("body")
        or raw.get("message")
        or raw.get("text")
        or ""
    )

    if not sms_body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="SMS body is empty.",
        )

    redis = get_redis()
    await redis.rpush(SMS_QUEUE_KEY, sms_body)
    logger.info("SMS queued (first 60 chars): %.60s", sms_body)
    return {"status": "queued"}


@app.post("/webhook/heartbeat", status_code=status.HTTP_200_OK)
async def heartbeat(
    x_sms_secret: Optional[str] = Header(default=None, alias="X-SMS-Secret"),
) -> dict:
    """
    Keep-alive ping from the Android SMS forwarder.
    Stores the current Unix timestamp in Redis so the watchdog can check it.
    """
    _verify_secret(x_sms_secret)

    redis = get_redis()
    ts = int(time.time())
    await redis.set(HEARTBEAT_KEY, ts, ex=600)  # auto-expire after 10 min
    logger.debug("Heartbeat received, ts=%s", ts)
    return {"status": "ok", "timestamp": ts}


@app.get("/health")
async def health() -> dict:
    """Simple liveness probe — no auth required."""
    return {"status": "ok"}

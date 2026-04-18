"""
Entry point — starts the Telegram bot, FastAPI webhook server, and SMS worker
concurrently in a single asyncio event loop.

Handles graceful shutdown on SIGINT / SIGTERM.
"""

import asyncio
import logging
import os
import signal
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from telegram.ext import Application, MessageHandler, filters

from bot.db.connection import close_pool, create_pool
from bot.handlers.deposit import build_deposit_handler
from bot.handlers.profile import build_profile_handlers
from bot.handlers.start import build_start_handler
from bot.services.sms_worker import run_worker
from bot.services.watchdog import build_scheduler
from bot.webhook.sms_receiver import app as fastapi_app, init_redis, close_redis

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Build Telegram application
# ---------------------------------------------------------------------------

def _build_application() -> Application:
    """Wire up all handlers and return the PTB Application."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    # Start / auth
    app.add_handler(build_start_handler())

    # Deposit (ConversationHandler must come before plain message handlers
    # so its entry_points are checked first)
    app.add_handler(build_deposit_handler())

    # Profile + back-to-menu (plain message handlers)
    for handler in build_profile_handlers():
        app.add_handler(handler)

    # Exit button (global fallback)
    from bot.handlers.start import cmd_exit
    app.add_handler(MessageHandler(filters.Regex(r"^Exit$"), cmd_exit))

    return app


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_stop_event: Optional[asyncio.Event] = None
_uvicorn_server: Optional["uvicorn.Server"] = None


def _handle_signal(sig: int) -> None:
    logger.info("Received signal %s — initiating shutdown.", signal.Signals(sig).name)
    if _stop_event:
        _stop_event.set()
    if _uvicorn_server:
        _uvicorn_server.should_exit = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    global _stop_event, _uvicorn_server
    _stop_event = asyncio.Event()

    # ---- Config ----
    redis_url = os.environ["REDIS_URL"]
    port = int(os.environ.get("PORT", 8000))
    admin_id_str = os.environ.get("ADMIN_TELEGRAM_ID", "")
    admin_id: Optional[int] = int(admin_id_str) if admin_id_str else None

    # ---- DB pool ----
    logger.info("Connecting to PostgreSQL...")
    await create_pool()

    try:
        await _run(redis_url, port, admin_id)
    finally:
        # Always close the pool — even if _run raises during init or runtime.
        await close_pool()


async def _run(redis_url: str, port: int, admin_id: Optional[int]) -> None:
    """Inner coroutine — all startup, runtime, and teardown except the DB pool."""
    global _uvicorn_server

    # ---- Webhook Redis client ----
    await init_redis(redis_url)

    # ---- Telegram bot ----
    tg_app = _build_application()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started (polling).")

    # ---- Watchdog scheduler ----
    scheduler = None
    if admin_id:
        scheduler = build_scheduler(tg_app.bot, redis_url, admin_id)
        scheduler.start()
        logger.info("Watchdog scheduler started.")
    else:
        logger.warning("ADMIN_TELEGRAM_ID not set — watchdog alerts disabled.")

    # ---- FastAPI (uvicorn) ----
    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level=_LOG_LEVEL.lower(),
        loop="none",  # share the existing event loop
    )
    _uvicorn_server = uvicorn.Server(uvicorn_config)

    # Register OS signals after uvicorn server is created so the handler
    # can set should_exit on it directly.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # ---- SMS worker ----
    sms_worker_task = asyncio.create_task(
        run_worker(redis_url, _stop_event),
        name="sms_worker",
    )

    # ---- Run uvicorn; wait for stop signal in parallel ----
    uvicorn_task = asyncio.create_task(
        _uvicorn_server.serve(),
        name="uvicorn",
    )

    # Block until Ctrl+C / SIGTERM sets the stop event
    await _stop_event.wait()

    logger.info("Shutting down...")

    # Tell uvicorn to stop accepting new connections and drain
    _uvicorn_server.should_exit = True
    await uvicorn_task

    # Cancel SMS worker (it checks stop_event via BLPOP timeout, but cancel is faster)
    sms_worker_task.cancel()
    try:
        await sms_worker_task
    except asyncio.CancelledError:
        pass

    # Stop scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)

    # Stop Telegram bot
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()

    # Close Redis
    await close_redis()

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())

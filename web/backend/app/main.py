"""FastAPI cockpit entry point.

Run:
    uvicorn app.main:app --host 127.0.0.1 --port 8081
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import audit as audit_mod
from .config import get_settings
from .routers import audit, auth, config, decisions, disclosures, flyer, health, pending, roster, safety, schedule, whatsapp

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tamper-evidence self-check (logs CRITICAL if audit log is not chattr +a)
    audit_mod.startup_self_check()

    # Background reaper for pair sessions
    reaper_task = asyncio.create_task(whatsapp._reap_loop())
    try:
        yield
    finally:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Shift Agent Cockpit API",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Same-origin only — Caddy serves frontend + API on same hostname.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],   # explicit empty; same-origin via reverse proxy
    allow_credentials=False,
    allow_methods=[],
    allow_headers=[],
)

# Public routes
app.include_router(health.router)

# Auth routes (some public, some not)
app.include_router(auth.router)

# Authenticated routes
app.include_router(roster.router)
app.include_router(schedule.router)
app.include_router(pending.router)
app.include_router(decisions.router)
app.include_router(config.router)
app.include_router(safety.router)
app.include_router(whatsapp.router)
app.include_router(disclosures.router)
app.include_router(audit.router)
app.include_router(flyer.router)

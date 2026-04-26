"""WhatsApp pairing router — status + SSE re-pair flow + unlink.

Per design v1.1: 2-step SSE flow (POST start, GET stream-by-token), resumable;
QR data emitted as raw Baileys data string; PairSession registry with reaper;
post-pair self_chat_jid auto-update in config.yaml.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..models import PairSessionResponse, WhatsAppStatus
from ..state import load_config, save_config

_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
import safe_io  # noqa: E402

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
settings = get_settings()


# ─── PairSession registry ──────────────────────────────────────────────


from pydantic import ConfigDict


class PairSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    sid: str
    pid: int
    pair_log: Path
    fout_fd: int  # so _kill_session can close the bridge stdout/stderr handle
    started_at: float
    expires_at: float
    last_client_seen_at: float


_pair_sessions: dict[str, PairSession] = {}
_PAIR_TTL = 180  # seconds; auto-kill after 3 min idle


async def _reap_loop():
    """Background reaper — kills idle pair sessions."""
    while True:
        try:
            now = time.time()
            for sid, sess in list(_pair_sessions.items()):
                if now - sess.last_client_seen_at > 60 or now > sess.expires_at:
                    _kill_session(sid)
        except Exception:
            pass
        await asyncio.sleep(15)


def _kill_session(sid: str) -> None:
    """Idempotent — terminate bridge, close fd, unlink scratch file."""
    sess = _pair_sessions.pop(sid, None)
    if sess is None:
        return
    try:
        os.kill(sess.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        os.close(sess.fout_fd)
    except OSError:
        pass
    sess.pair_log.unlink(missing_ok=True)


# ─── Status ────────────────────────────────────────────────────────────


@router.get("/status", response_model=WhatsAppStatus)
async def status(_=Depends(require_auth)):
    creds = settings.hermes_creds_json
    me_id: str | None = None
    self_chat_jid: str | None = None
    paired = False
    bridge_uptime: float | None = None
    bridge_status: str | None = None

    if creds.exists():
        try:
            data = json.loads(creds.read_text())
            me_id = data.get("me", {}).get("id")
            paired = bool(me_id)
            if me_id:
                phone = me_id.split(":")[0]
                self_chat_jid = f"{phone}@s.whatsapp.net"
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(settings.bridge_health_url)
            if r.status_code == 200:
                d = r.json()
                bridge_uptime = d.get("uptime")
                bridge_status = d.get("status")
    except Exception:
        pass

    return WhatsAppStatus(
        paired=paired,
        me_id=me_id,
        self_chat_jid=self_chat_jid,
        bridge_uptime_seconds=bridge_uptime,
        bridge_status=bridge_status,
        last_seen_at=None,  # Phase 3
    )


# ─── Re-pair: POST start ───────────────────────────────────────────────


@router.post("/repair", response_model=PairSessionResponse)
async def start_repair(request: Request, _=Depends(require_fresh_otp)):
    # 409 if active session exists
    now = time.time()
    for sess in _pair_sessions.values():
        if now < sess.expires_at:
            raise HTTPException(409, "pair session already active — cancel first or wait")

    sid = secrets.token_hex(12)
    settings.pair_runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    pair_log = settings.pair_runtime_dir / f"cockpit-pair-{sid}.txt"
    pair_log.write_text("")
    pair_log.chmod(0o600)

    # Stop hermes-gateway (which holds the bridge), wipe session, start --pair-only bridge.
    # Sudoers rule provisioned in deploy.sh allows shift-agent → /usr/bin/systemctl stop|start hermes-gateway.
    subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", "hermes-gateway"],
        check=False, timeout=10, shell=False,
    )
    # Backup + clear session
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    bak = settings.hermes_session_dir.parent / f"session.bak-{ts}"
    if settings.hermes_session_dir.exists():
        os.rename(settings.hermes_session_dir, bak)
    settings.hermes_session_dir.mkdir(parents=True, exist_ok=True)

    # Validate bridge paths are within expected dirs (defense-in-depth)
    if not str(settings.bridge_node_bin).startswith("/root/.hermes/node/"):
        raise HTTPException(500, "bridge node binary path failed validation")
    if not str(settings.bridge_js).startswith("/root/.hermes/hermes-agent/"):
        raise HTTPException(500, "bridge js path failed validation")

    # Spawn bridge --pair-only with stdbuf -oL for line-buffered stdout
    fout_fd = os.open(str(pair_log), os.O_WRONLY | os.O_APPEND)
    proc = subprocess.Popen(
        [
            "/usr/bin/stdbuf",
            "-oL",
            "-eL",
            str(settings.bridge_node_bin),
            str(settings.bridge_js),
            "--port",
            "3000",
            "--session",
            str(settings.hermes_session_dir),
            "--mode",
            "self-chat",
            "--pair-only",
        ],
        stdout=fout_fd,
        stderr=fout_fd,
        stdin=subprocess.DEVNULL,
        cwd=str(settings.bridge_js.parent),
        shell=False,
    )

    expires_at = time.time() + _PAIR_TTL
    _pair_sessions[sid] = PairSession(
        sid=sid,
        pid=proc.pid,
        pair_log=pair_log,
        fout_fd=fout_fd,
        started_at=time.time(),
        expires_at=expires_at,
        last_client_seen_at=time.time(),
    )
    audit_log(
        "whatsapp.repair.start",
        ip=client_ip(request),
        ua=client_ua(request),
        details={"sid": sid, "pid": proc.pid, "session_backup": str(bak)},
    )
    return PairSessionResponse(
        session_id=sid,
        expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    )


# ─── Re-pair: GET stream ───────────────────────────────────────────────


@router.get("/repair/{sid}/stream")
async def repair_stream(sid: str, request: Request, _=Depends(require_auth)):
    sess = _pair_sessions.get(sid)
    if not sess:
        raise HTTPException(404, "session not found or expired")

    async def event_gen():
        last_pos = 0
        complete = False
        # qrcode-terminal renders the QR using these unicode block characters.
        # We forward raw block-art lines as 'qr_line' events so the frontend
        # can render them as a <pre> ASCII QR (proven to scan in this project's
        # rehearsal flow). bridge.js does NOT emit the raw Baileys QR string.
        QR_CHARS = set("▄█▀ ")
        try:
            sess.last_client_seen_at = time.time()
            while not complete:
                if await request.is_disconnected():
                    return
                sess.last_client_seen_at = time.time()
                if time.time() > sess.expires_at:
                    yield {"event": "error", "data": json.dumps({"message": "session expired"})}
                    return
                try:
                    content = sess.pair_log.read_text()
                except FileNotFoundError:
                    yield {"event": "error", "data": json.dumps({"message": "log gone"})}
                    return
                new = content[last_pos:]
                last_pos = len(content)
                for line in new.splitlines():
                    if "Pairing complete" in line or "Credentials saved" in line:
                        me_id = None
                        try:
                            data = json.loads(settings.hermes_creds_json.read_text())
                            me_id = data.get("me", {}).get("id")
                        except Exception:
                            pass
                        if me_id:
                            phone = me_id.split(":")[0]
                            new_jid = f"{phone}@s.whatsapp.net"
                            try:
                                cfg = load_config()
                                cfg.owner.self_chat_jid = new_jid
                                save_config(cfg)
                            except Exception:
                                pass
                            yield {"event": "complete", "data": json.dumps({"me": me_id, "self_chat_jid": new_jid})}
                        else:
                            yield {"event": "complete", "data": json.dumps({})}
                        complete = True
                        break
                    elif "WhatsApp connected" in line:
                        yield {"event": "connected", "data": json.dumps({})}
                    elif "stream errored" in line.lower():
                        yield {"event": "error", "data": json.dumps({"message": line[:200]})}
                    elif line and len(line) >= 30 and all((ch in QR_CHARS) for ch in line):
                        # ASCII-block QR row — frontend renders as <pre>
                        yield {"event": "qr_line", "data": line}
                    elif line.strip():
                        yield {"event": "log", "data": line[:500]}
                await asyncio.sleep(1)
        finally:
            # ALWAYS clean up the bridge process + scratch file, even on disconnect/exception.
            # Closes the security gap where an attacker could observe QR via a re-opened stream
            # while the original tab was closed.
            _kill_session(sid)
            subprocess.run(
                ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "hermes-gateway"],
                check=False, timeout=15, shell=False,
            )

    return EventSourceResponse(event_gen())


@router.post("/repair/{sid}/cancel")
async def cancel_repair(sid: str, request: Request, _=Depends(require_auth)):
    if sid not in _pair_sessions:
        raise HTTPException(404)
    _kill_session(sid)
    audit_log("whatsapp.repair.cancel", ip=client_ip(request), ua=client_ua(request), details={"sid": sid})
    return {"ok": True}


# ─── Unlink ────────────────────────────────────────────────────────────


@router.post("/unlink")
async def unlink(request: Request, _=Depends(require_fresh_otp)):
    """Wipe the WA session — owner must re-pair."""
    subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", "hermes-gateway"],
        check=False, timeout=10, shell=False,
    )
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    bak = settings.hermes_session_dir.parent / f"session.unlinked-{ts}"
    if settings.hermes_session_dir.exists():
        os.rename(settings.hermes_session_dir, bak)
    settings.hermes_session_dir.mkdir(parents=True, exist_ok=True)
    audit_log("whatsapp.unlink", ip=client_ip(request), ua=client_ua(request), details={"backup": str(bak)})
    return {"ok": True, "backup": str(bak)}

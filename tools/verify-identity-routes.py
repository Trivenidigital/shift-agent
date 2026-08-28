#!/usr/bin/env python3
"""Route-level proof that the DEPLOYED cf-router routes authority correctly.

Drives the registered wrapper `pre_gateway_dispatch` from /root/.hermes/plugins
against COPIED state, with every state-mutating and transport call stubbed.

The WRAPPER, deliberately -- not `_pre_gateway_dispatch_impl`. The wrapper owns
the turn-identity memo and the inbound-dedupe gate, so calling the impl
directly exercises a path production never takes. Driving the impl was itself
one of the defects this verifier hit during the c6eddc4c deployment.

Production state is protected by MECHANISM, not by intention: `load_plugin`
enumerates every module-level Path under the live SHIFT_ROOT and redirects it
into a temp dir, so a store this script has never heard of is still redirected.
An earlier version asserted "production state files are never opened for write"
while writing synthetic entries into the production cf-router inbound-dedupe
store -- because it redirected a hand-picked list of four paths. A claim with no
mechanism behind it cannot notice when it stops being true.

Both guarantees are then ASSERTED rather than trusted: the stubs record calls
instead of performing them (so no message is sent), and the run compares the
production proposal store byte-for-byte, requiring it to be PRESENT so the
comparison cannot pass by both sides being absent.

Proves, for the dual-role principal:
  F8 owner approval by LID       -> handle_owner_command, sender_role=owner
  F8 owner approval by phone-JID -> handle_owner_command, sender_role=owner
  candidate YES/NO either form   -> handle_candidate_response, sender_role=employee

Usage:  python3 verify-identity-routes.py
"""
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

SHIFT = Path("/opt/shift-agent")
PLUGIN_DIR = Path("/root/.hermes/plugins/cf-router")
IDENTIFY = Path("/usr/local/bin/identify-sender")
CONFIG = SHIFT / "config.yaml"
ROSTER = SHIFT / "roster.json"
# The deployed plugin's own constant, not a guess.
PROD_PENDING = SHIFT / "state" / "pending.json"

_SECRET_TOKENS = ("token", "key", "secret", "password", "webhook", "credential",
                  "pushover", "openrouter", "telegram", "apikey", "auth")


def _is_secret(k):
    return any(t in str(k).lower() for t in _SECRET_TOKENS)


def _redact(n):
    if isinstance(n, dict):
        return {k: (("REDACTED" if not isinstance(v, (dict, list)) else _redact(v))
                    if _is_secret(k) else _redact(v)) for k, v in n.items()}
    if isinstance(n, list):
        return [_redact(x) for x in n]
    return n


def _secrets(n, acc):
    if isinstance(n, dict):
        for k, v in n.items():
            if _is_secret(k) and isinstance(v, str) and len(v) >= 8:
                acc.add(v)
            _secrets(v, acc)
    elif isinstance(n, list):
        for x in n:
            _secrets(x, acc)
    return acc


def load_plugin(tag, work, env):
    pkg = "cf_router_route_%s" % tag
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    spec_pkg = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    mod_pkg = importlib.util.module_from_spec(spec_pkg)
    mod_pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg] = mod_pkg
    mods = {}
    for name in ("actions", "hooks"):
        spec = importlib.util.spec_from_file_location(
            "%s.%s" % (pkg, name), PLUGIN_DIR / ("%s.py" % name))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["%s.%s" % (pkg, name)] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    actions, hooks = mods["actions"], mods["hooks"]

    actions.CONFIG_PATH = work / "config.yaml"
    actions.ROSTER_PATH = work / "roster.json"
    actions.PENDING_PATH = work / "pending.json"
    actions.LEADS_PATH = work / "leads.json"
    actions.LOG_PATH = work / "decisions.log"
    actions.LEADS_PATH.write_text('{"leads": []}', encoding="utf-8")

    # Redirect EVERY remaining module-level Path that points inside the live
    # SHIFT_ROOT. The first version of this script redirected only the four
    # paths I thought of and then wrote synthetic dedupe entries into
    # production `cf-router-inbound-dedupe.json` -- while its own docstring
    # claimed production files are never opened for write. Enumerating the
    # module is the only version of this that stays true as the plugin grows.
    redirected = []
    for name in dir(actions):
        if not name.isupper():
            continue
        val = getattr(actions, name, None)
        if not isinstance(val, Path):
            continue
        try:
            rel = val.relative_to(SHIFT)
        except ValueError:
            continue
        target = work / "redirected" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        setattr(actions, name, target)
        redirected.append(name)
    actions._VERIFY_REDIRECTED = redirected

    def spawn(identifier):
        p = subprocess.run([sys.executable, str(IDENTIFY), identifier],
                           capture_output=True, text=True, timeout=30, env=env)
        if p.returncode != 0:
            return actions._IdentityResolution(False, {})
        try:
            return actions._IdentityResolution(True, json.loads(p.stdout))
        except json.JSONDecodeError:
            return actions._IdentityResolution(False, {})

    actions._invoke_identify_sender = spawn
    calls = []
    actions.invoke_update_proposal_status = (
        lambda *a, **k: calls.append(("update_proposal_status", a, k)) or 0)
    actions.invoke_send_coverage_message = (
        lambda pid: calls.append(("send_coverage_message", pid)) or 0)
    actions.invoke_shift_sick_call = (
        lambda **k: calls.append(("shift_sick_call", k)) or (0, "", ""))
    actions.fire_pushover_alert = lambda *a, **k: calls.append(("pushover",)) or None
    if hasattr(actions, "_bridge_post"):
        actions._bridge_post = lambda *a, **k: calls.append(("bridge_post",)) or (False, "stubbed")
    return hooks, actions, calls


def routed_rows(actions):
    if not actions.LOG_PATH.exists():
        return []
    out = []
    for line in actions.LOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return [r for r in out if r.get("type") == "dispatcher_routed"]


def seed(actions, candidate, status="awaiting_owner_approval", code="#ABCDE"):
    actions.PENDING_PATH.write_text(json.dumps({"proposals": {"P0001": {
        "proposal_id": "P0001", "code": code, "status": status,
        "candidate_employee_id": candidate, "candidate_name": "Dual Principal",
        "absent_employee_id": "e001", "absent_date": "2026-09-01",
        "absent_shift": "09:00-17:00", "absent_role": "cashier",
        "created_ts": "2026-08-24T10:00:00+00:00"}}}), encoding="utf-8")
    if actions.LOG_PATH.exists():
        actions.LOG_PATH.unlink()


_SEQ = [0]


def inbound(chat_id, text):
    """Unique message_id per call: cf-router dedupes on (chat_id, message_id),
    so a reused id makes every route after the first look dead."""
    _SEQ[0] += 1
    return SimpleNamespace(chat_id=chat_id, text=text,
                           message_id="wamid.ROUTEVERIFY%03d" % _SEQ[0],
                           from_me=False, media_path=None)


def main():
    work = Path(tempfile.mkdtemp(prefix="route-verify-"))
    results = []
    prod_before = PROD_PENDING.read_bytes() if PROD_PENDING.exists() else None
    try:
        raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        secs = _secrets(copy.deepcopy(raw), set())
        if not secs:
            raise SystemExit("REFUSING: redactor found no secrets; token list stale")
        (work / "config.yaml").write_text(yaml.safe_dump(_redact(raw)), encoding="utf-8")
        blob = (work / "config.yaml").read_text(encoding="utf-8")
        assert not [x for x in secs if x in blob], "REFUSING: secret survived redaction"
        shutil.copy2(ROSTER, work / "roster.json")

        env = {k: v for k, v in os.environ.items()
               if not any(t in k.lower() for t in _SECRET_TOKENS)}
        env["SHIFT_AGENT_CONFIG_PATH"] = str(work / "config.yaml")
        env["SHIFT_AGENT_ROSTER_PATH"] = str(work / "roster.json")
        env["PYTHONPATH"] = str(SHIFT / "src" / "platform")

        cfg = yaml.safe_load((work / "config.yaml").read_text(encoding="utf-8"))
        owner = cfg.get("owner", {})
        alias = [a.get("phone") for a in owner.get("authorized_identities", [])
                 if isinstance(a, dict) and a.get("phone")][0]
        roster = json.loads((work / "roster.json").read_text(encoding="utf-8"))
        emp = [e for e in roster["employees"] if e.get("phone") == alias][0]
        forms = [("LID", emp["lid"]), ("phone-JID", alias.lstrip("+") + "@s.whatsapp.net")]

        def check(name, ok, detail):
            results.append((name, bool(ok), detail))

        # ---- F8 owner approval, both identifier forms ----
        for label, ident in forms:
            hooks, actions, calls = load_plugin("f8" + label[:3].lower(), work, env)
            seed(actions, candidate=emp["id"])
            hooks.pre_gateway_dispatch(inbound(ident, "#ABCDE"))
            rows = routed_rows(actions)
            skills = [r.get("routed_to_skill") for r in rows]
            roles = [r.get("sender_role") for r in rows]
            check("F8 owner approval via %s reaches handle_owner_command" % label,
                  skills == ["handle_owner_command"], {"skills": skills})
            check("F8 via %s audits sender_role=owner" % label,
                  roles == ["owner"], {"sender_role": roles})
            check("F8 via %s actually invoked the status update" % label,
                  any(c[0] == "update_proposal_status" for c in calls),
                  {"calls": [c[0] for c in calls]})

        # ---- candidate YES/NO, both identifier forms ----
        for label, ident in forms:
            for text, expect in (("YES", "accepted"), ("NO", "declined")):
                hooks, actions, calls = load_plugin(
                    "cand%s%s" % (label[:3].lower(), text.lower()), work, env)
                seed(actions, candidate=emp["id"], status="sent")
                hooks.pre_gateway_dispatch(inbound(ident, text))
                rows = routed_rows(actions)
                skills = [r.get("routed_to_skill") for r in rows]
                roles = [r.get("sender_role") for r in rows]
                check("candidate %s via %s -> handle_candidate_response" % (text, label),
                      skills == ["handle_candidate_response"], {"skills": skills})
                check("candidate %s via %s audits sender_role=employee" % (text, label),
                      roles == ["employee"], {"sender_role": roles})

        # ---- production untouched ----
        after = PROD_PENDING.read_bytes() if PROD_PENDING.exists() else None
        check("production proposal store byte-unchanged (and PRESENT)",
              prod_before is not None and after == prod_before,
              {"sha_before": hashlib.sha256(prod_before).hexdigest()[:16] if prod_before else None,
               "sha_after": hashlib.sha256(after).hexdigest()[:16] if after else None})

        print("ROUTE-LEVEL AUTHORITY VERIFICATION (deployed plugin, copied state)")
        print("=" * 76)
        fails = 0
        for name, ok, detail in results:
            fails += 0 if ok else 1
            print("  [%s] %-56s %s" % ("PASS" if ok else "FAIL", name[:56],
                                       json.dumps(detail)[:60]))
        print("=" * 76)
        print("VERDICT: %s  (%d checks, %d failed)"
              % ("ALL PASS" if not fails else "FAILURES PRESENT", len(results), fails))
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

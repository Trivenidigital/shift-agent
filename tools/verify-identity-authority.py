#!/usr/bin/env python3
"""Post-deploy verification of the dual-role identity/authority invariants.

Runs ON THE BOX, READ-ONLY against production state. Nothing is mutated, no
message is sent, no proposal is created or transitioned.

Credential-sterile by construction
----------------------------------
The production config is copied to a scratch dir and every secret-shaped key
is REPLACED before `identify-sender` is pointed at it. An earlier rehearsal in
this programme copied state faithfully and carried live Pushover keys with it;
copying is not sterilising. The sterilisation is asserted, not assumed --
`_assert_sterile` re-reads the written file and fails if any known secret
value survives.

`identify-sender` needs only `owner.phone`, `owner.self_chat_jid`,
`owner.lid` and `owner.authorized_identities`, so redacting the rest cannot
change the answers under test.

Usage:  python3 verify-identity-authority.py            # verify
        python3 verify-identity-authority.py --json     # machine-readable
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH", "/opt/shift-agent/config.yaml"))
# The roster lives at the SHIFT_ROOT, not under state/. Resolved rather than
# assumed: the first spelling of this constant was state/roster.json and the
# verifier died on FileNotFoundError against the real box.
ROSTER = Path(os.environ.get("SHIFT_AGENT_ROSTER_PATH", "/opt/shift-agent/roster.json"))
IDENTIFY = Path("/usr/local/bin/identify-sender")
PLATFORM = Path("/opt/shift-agent/src/platform")

# SUBSTRING tokens, not exact key names. The first version of this used an
# exact-match set and silently redacted NOTHING against the real config, whose
# keys are `alerting.pushover_user_key` / `alerting.pushover_app_token` -- so
# `_assert_sterile` passed vacuously on an empty set. A sterility guard that
# cannot find the secrets is worse than none, because it reports success.
_SECRET_TOKENS = (
    "token", "key", "secret", "password", "passwd", "webhook", "credential",
    "pushover", "openrouter", "telegram", "apikey", "auth",
)


def _is_secret_key(k) -> bool:
    return any(t in str(k).lower() for t in _SECRET_TOKENS)
_REDACTED = "REDACTED-BY-VERIFIER"


def _redact(node):
    """Recursively replace secret-shaped values. Structure is preserved so the
    config still parses; only the values a leak would care about are gone."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if _is_secret_key(k):
                out[k] = _REDACTED if not isinstance(v, (dict, list)) else _redact(v)
            else:
                out[k] = _redact(v)
        return out
    if isinstance(node, list):
        return [_redact(x) for x in node]
    return node


def _collect_secret_values(node, acc):
    if isinstance(node, dict):
        for k, v in node.items():
            if _is_secret_key(k) and isinstance(v, str) and len(v) >= 8:
                acc.add(v)
            _collect_secret_values(v, acc)
    elif isinstance(node, list):
        for x in node:
            _collect_secret_values(x, acc)
    return acc


def _assert_sterile(written: Path, originals: set):
    """Prove the sterilisation worked instead of trusting that it did."""
    blob = written.read_text(encoding="utf-8", errors="replace")
    if not originals:
        raise SystemExit(
            "REFUSING TO RUN: the redactor found NO secret-shaped values in the "
            "config. Either the config genuinely has none, or the token list no "
            "longer matches its key names -- and the second case makes every "
            "sterility claim below vacuous. Verify before proceeding.")
    leaked = sorted(s for s in originals if s and s in blob)
    if leaked:
        raise SystemExit(
            "REFUSING TO RUN: %d secret value(s) survived redaction in %s"
            % (len(leaked), written))
    return len(originals)


def build_sterile_state(work: Path):
    import yaml
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    secrets = _collect_secret_values(copy.deepcopy(raw), set())
    cfg = work / "config.yaml"
    cfg.write_text(yaml.safe_dump(_redact(raw)), encoding="utf-8")
    n = _assert_sterile(cfg, secrets)
    ros = work / "roster.json"
    shutil.copy2(ROSTER, ros)
    env = os.environ.copy()
    # Drop inherited secrets too -- the file is not the only carrier.
    for k in list(env):
        if any(s in k.lower() for s in ("token", "secret", "key", "password",
                                        "pushover", "webhook")):
            env.pop(k, None)
    env["SHIFT_AGENT_CONFIG_PATH"] = str(cfg)
    env["SHIFT_AGENT_ROSTER_PATH"] = str(ros)
    env["PYTHONPATH"] = str(PLATFORM)
    return env, raw, json.loads(ros.read_text(encoding="utf-8")), n


def resolve(env, identifier):
    p = subprocess.run([sys.executable, str(IDENTIFY), identifier],
                       capture_output=True, text=True, timeout=30, env=env)
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"_stdout": p.stdout[:200], "_stderr": p.stderr[-200:]}


def _load_is_owner_chat(env):
    """Import the LIVE cf-router actions and return its `is_owner_chat`.

    Pointed at the sterile copies via module globals, so the boundary is
    exercised against the same state the resolver saw. Returns None if the
    plugin cannot be loaded -- reported as a failed check, never skipped
    silently.
    """
    import importlib.machinery, importlib.util
    plugin = Path("/root/.hermes/plugins/cf-router/actions.py")
    if not plugin.exists():
        return None
    try:
        pkg = "cf_router_verify_pkg"
        spec_pkg = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
        mod_pkg = importlib.util.module_from_spec(spec_pkg)
        mod_pkg.__path__ = [str(plugin.parent)]
        sys.modules[pkg] = mod_pkg
        spec = importlib.util.spec_from_file_location("%s.actions" % pkg, plugin)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["%s.actions" % pkg] = mod
        spec.loader.exec_module(mod)
        mod.CONFIG_PATH = Path(env["SHIFT_AGENT_CONFIG_PATH"])
        mod.ROSTER_PATH = Path(env["SHIFT_AGENT_ROSTER_PATH"])
        real = mod._invoke_identify_sender

        def spawn(identifier):
            p = subprocess.run([sys.executable, str(IDENTIFY), identifier],
                               capture_output=True, text=True, timeout=30, env=env)
            if p.returncode != 0:
                return mod._IdentityResolution(False, {})
            try:
                return mod._IdentityResolution(True, json.loads(p.stdout))
            except json.JSONDecodeError:
                return mod._IdentityResolution(False, {})
        mod._invoke_identify_sender = spawn
        return mod.is_owner_chat
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="identity-verify-"))
    results, failures = [], []
    try:
        env, cfg, roster, n_secrets = build_sterile_state(work)

        def check(name, ok, detail):
            results.append({"check": name, "ok": bool(ok), "detail": detail})
            if not ok:
                failures.append(name)

        owner = cfg.get("owner", {})
        owner_jid = owner.get("self_chat_jid", "")
        aliases = [a.get("phone") for a in owner.get("authorized_identities", [])
                   if isinstance(a, dict) and a.get("phone")]
        emps = roster.get("employees", [])
        by_phone = {e.get("phone"): e for e in emps if e.get("phone")}

        # 1-2. The dual principal: same memberships by BOTH identifiers.
        for alias in aliases:
            emp = by_phone.get(alias)
            jid = alias.lstrip("+") + "@s.whatsapp.net"
            rc_j, doc_j = resolve(env, jid)
            forms = [("phone-JID", jid, rc_j, doc_j)]
            if emp and emp.get("lid"):
                rc_l, doc_l = resolve(env, emp["lid"])
                forms.append(("LID", emp["lid"], rc_l, doc_l))
            roles = {}
            for label, ident, rc, doc in forms:
                r = sorted(doc.get("roles") or [])
                roles[label] = r
                check("dual/%s/%s owner membership" % (alias, label),
                      rc == 0 and "owner" in r, {"identifier": ident, "roles": r})
                if emp:
                    check("dual/%s/%s employee membership" % (alias, label),
                          "employee" in r, {"roles": r})
            if len(roles) == 2:
                vals = list(roles.values())
                check("dual/%s memberships IDENTICAL across identifiers" % alias,
                      vals[0] == vals[1], roles)

        # 3. Primary owner resolves as owner.
        if owner_jid:
            rc, doc = resolve(env, owner_jid)
            check("primary owner self_chat_jid -> owner",
                  rc == 0 and "owner" in (doc.get("roles") or []),
                  {"identifier": owner_jid, "roles": doc.get("roles")})

        # 4. Ordinary employees must NOT hold owner membership, either form.
        alias_set = set(aliases)
        checked = 0
        for e in emps:
            if e.get("phone") in alias_set or e.get("status") != "active":
                continue
            for ident in filter(None, [
                    (e["phone"].lstrip("+") + "@s.whatsapp.net") if e.get("phone") else None,
                    e.get("lid")]):
                rc, doc = resolve(env, ident)
                r = doc.get("roles") or []
                check("employee %s (%s) is NOT owner" % (e.get("id"), ident),
                      rc == 0 and "owner" not in r, {"roles": r})
                checked += 1
        check("at least one ordinary-employee negative was exercised",
              checked > 0, {"negatives_run": checked})

        # 4b. The AUTHORIZATION BOUNDARY itself, not just membership.
        #     `is_owner_chat` is what F8 and automation-control actually call,
        #     and it applies a shape allowlist that membership does not. These
        #     shapes RESOLVE as owner through identify-sender and must still be
        #     refused here -- checking membership alone would miss that.
        boundary = _load_is_owner_chat(env)
        if boundary is None:
            check("is_owner_chat boundary loadable", False,
                  {"note": "cf-router actions could not be imported"})
        else:
            digits = owner_jid.split("@")[0] if owner_jid else ""
            hostile = [
                ("%s@g.us" % digits, "group JID carrying owner digits"),
                ("%s@lid" % owner_jid, "malformed double-suffix JID"),
                (digits, "bare digits"),
                ("  %s  " % owner_jid, "whitespace-padded JID"),
                ("status@broadcast", "broadcast address"),
            ]
            for ident, why in hostile:
                check("boundary REFUSES %s" % why, boundary(ident) is False,
                      {"identifier": ident})
            # ...and still admits the supported forms.
            check("boundary admits primary owner JID", boundary(owner_jid) is True,
                  {"identifier": owner_jid})
            for alias in aliases:
                jid = alias.lstrip("+") + "@s.whatsapp.net"
                check("boundary admits authorized phone-JID", boundary(jid) is True,
                      {"identifier": jid})
                emp = by_phone.get(alias)
                if emp and emp.get("lid"):
                    check("boundary admits authorized LID",
                          boundary(emp["lid"]) is True, {"identifier": emp["lid"]})
            for e in emps:
                if e.get("phone") in set(aliases) or e.get("status") != "active":
                    continue
                jid = e["phone"].lstrip("+") + "@s.whatsapp.net"
                check("boundary refuses ordinary employee %s" % e.get("id"),
                      boundary(jid) is False, {"identifier": jid})
                break

        # 5. Unknown identity -> not owner, not employee.
        rc, doc = resolve(env, "19999999999@s.whatsapp.net")
        check("unknown identity has no memberships",
              not (doc.get("roles") or []), {"rc": rc, "roles": doc.get("roles")})

        # 6. Ambiguity fails closed. Uses a COPY with an injected duplicate --
        #    production roster is never written.
        dup = copy.deepcopy(roster)
        if aliases and dup.get("employees"):
            dup["employees"].append({
                "id": "e999", "name": "Ambiguity Probe",  # EmployeeId is ^e\d{3,}$
                "role": "floor", "phone": aliases[0], "status": "active",
                "languages": ["en"], "can_cover_roles": ["floor"]})
            dpath = work / "roster-dup.json"
            dpath.write_text(json.dumps(dup), encoding="utf-8")
            denv = dict(env, SHIFT_AGENT_ROSTER_PATH=str(dpath))
            ident = aliases[0].lstrip("+") + "@s.whatsapp.net"
            rc, doc = resolve(denv, ident)
            # rc != 0 alone is NOT enough: an invalid probe row makes the
            # whole roster fail to load (rc=5) and the check passes for the
            # wrong reason. Demand the AMBIGUITY refusal specifically.
            err = str(doc.get("error") or "")
            check("duplicate identifier fails closed via AMBIGUITY refusal",
                  rc != 0 and "ambiguous_identifier" in err
                  and "owner" not in (doc.get("roles") or []),
                  {"rc": rc, "error": err[:120]})
            check("production roster untouched by the ambiguity probe",
                  json.loads(ROSTER.read_text(encoding="utf-8")) == roster, {})

        print(json.dumps({"results": results, "failures": failures,
                          "secrets_redacted": n_secrets},
                         indent=2) if args.json else "")
        if not args.json:
            print("IDENTITY / AUTHORITY VERIFICATION  (read-only, %d secrets redacted)"
                  % n_secrets)
            print("=" * 74)
            for r in results:
                print("  [%s] %-58s %s"
                      % ("PASS" if r["ok"] else "FAIL", r["check"][:58],
                         json.dumps(r["detail"])[:70]))
            print("=" * 74)
            print("VERDICT: %s  (%d checks, %d failed)"
                  % ("ALL PASS" if not failures else "FAILURES PRESENT",
                     len(results), len(failures)))
        return 1 if failures else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

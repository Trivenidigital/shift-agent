# Hermes Version Monitoring (Track A durability) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** `extends-Hermes` — adds a new read-only runtime monitor + systemd timer + deploy wiring on top of existing Hermes pin infrastructure. Reuses the existing notify chokepoint, baseline file, and `safe_io` helpers without modification. No new storage engine, no new approval-code generator, no dispatcher change.

**Goal:** Ship an on-VPS, read-only Hermes version monitor (systemd timer) that verifies live runtime Hermes pin state against the shipped baseline, does a best-effort fail-safe upstream-ahead check, writes a report, and alerts the operator on-change — closing the between-deploy runtime blind spot the existing weekly CI drift workflow cannot see.

**Architecture:** A Python script (`hermes-version-check`) runs from a daily systemd timer as `User=shift-agent`. It performs network-free local checks (live Hermes commit + `bridge.js` sha256 + patch markers vs the deploy-snapshotted baseline) plus a best-effort `git ls-remote` upstream check (fail-safe). It writes a JSON report every run, persists alert-signature state for notify-on-change throttling, and routes operator alerts through the existing `shift-agent-notify-owner` chokepoint. `OnFailure=` wires a failure-notify service for monitor-process crashes. It performs **zero** runtime mutation: no Hermes update, no gateway restart, no baseline rewrite, no skill install, no `patch-hermes.py`, no clone/fetch/checkout, no auto-update.

**Tech Stack:** Python 3.11+ **stdlib only** (`argparse`, `subprocess`, `hashlib`, `json`, `os`, `pathlib`). Report + state are monitor-private single-writer files written via inline atomic `os.replace` (portable; no `fcntl`/`safe_io` import → script imports cleanly on the Linux VPS *and* the Windows test host). Alert traceability (dispatched/delivered) prints to the monitor's own stdout → systemd `StandardOutput` log; the monitor does **not** write the shared `decisions.log` audit chain. systemd `.service`/`.timer`; alerts reuse the existing `shift-agent-notify-owner` chokepoint (Pushover→WhatsApp).

---

## Global Constraints

- **MONITORING ONLY.** The script and all wiring MUST NOT: update Hermes; restart the gateway; rewrite `tools/hermes-patch-baseline.txt`; mutate any Hermes file; install/update skills; enable auto-update; run `tools/patch-hermes.py`; clone/fetch/checkout upstream; begin 0.17 patch-port; begin WhatsApp Business Cloud migration; touch Flyer Studio.
- **Upstream read is `git ls-remote` ONLY.** No clone, no fetch, no patch dry-run. The heavy patch-port dry-run stays in the existing `.github/workflows/hermes-drift-check.yml`.
- **Network failure is fail-safe, not fail-closed.** A failed upstream check still completes local checks, still writes the report, records `upstream_status=unknown`, and throttles alerts.
- **`mutation_performed=false`** MUST appear in every report and be provable by a before/after byte-identity test on Hermes home + baseline.
- **Runs as `User=shift-agent`** (least privilege, fleet-consistent) with `git -c safe.directory=*` + `readlink -f` symlink resolution (mirrors `tools/check-shift-agent-patch.sh:67-70`).
- **Reuse notify chokepoint:** alerts go through `/usr/local/bin/shift-agent-notify-owner` (override for tests via env `SHIFT_AGENT_NOTIFY_OWNER_BIN`, mirroring `tests/test_flyer_recovery_watchdog.py:1299`). No new alert substrate.
- **No `schemas.py` config changes.** Timer is unconditional (Hermes exists on every VPS). Paths/repo are CLI flags with production defaults.
- **Windows-safe tests:** guard POSIX-only permission checks with `@pytest.mark.skipif(platform.system()=="Windows", ...)` (mirrors `tests/test_catering_v02_scripts.py:21-24`).

---

## Drift-check findings (CLAUDE.md §7a — partial primitive already exists)

| Existing primitive | File | Covers | Residual gap this PR fills |
|---|---|---|---|
| Deploy-time pin gate | `tools/check-shift-agent-patch.sh` | live commit + bridge sha + markers vs baseline — **deploy time only** | between-deploy runtime drift (timer) |
| Weekly CI drift monitor | `.github/workflows/hermes-drift-check.yml` | upstream clone + `patch-hermes.py` dry-run + GitHub issue — **weekly, CI-side** | operator alert via real channel; on-box report; runtime visibility |
| Pin baseline | `tools/hermes-patch-baseline.txt` | `HERMES_COMMIT`/`HERMES_VERSION`/`BRIDGE_POST_PATCH_SHA256` | not installed to a runtime path → snapshot it read-only |

**Verdict:** partial match, not closure. This PR is additive runtime-state monitoring; it deliberately delegates heavy patch-port detection to the existing CI workflow.

## Hermes-first analysis

Per-step `[Hermes]` / `[net-new]` tag of every step the monitor takes:

| Step | Hermes / ecosystem capability? | Tag |
|---|---|---|
| Read live Hermes git commit | Hermes exposes no self-version primitive; `git rev-parse` is stdlib subprocess | `[net-new]` (trivial) |
| Hash `bridge.js` | `hashlib` stdlib | `[net-new]` (trivial) |
| Read baseline KEY=VALUE | plain file read | `[net-new]` (trivial) |
| Upstream `git ls-remote` | not a Hermes capability; CI uses git clone, we use lighter ls-remote | `[net-new]` |
| Compare + classify conditions | pure logic — the monitor brain | `[net-new]` |
| Operator alert delivery | **`shift-agent-notify-owner` chokepoint (Pushover→WhatsApp)** | `[Hermes]` reuse |
| Atomic report/state write | inline `os.replace` (monitor-private single-writer files; no shared-writer contention → no `fcntl` needed) | `[net-new]` (trivial) |
| Alert traceability | dispatched/delivered to monitor's own stdout → systemd log (not the shared `decisions.log`) | `[net-new]` (trivial) |
| Periodic scheduling | systemd timer (platform convention) | `[net-new]` (config) |

Hermes skill hub check: version-monitoring of the Hermes runtime itself is operator infrastructure, not an agent task — no Hermes/OpenClaw/community skill applies. awesome-hermes-agent ecosystem: none cover "monitor my pinned Hermes for upstream drift from the customer VPS." **Verdict:** net-new is only the ~140-LOC read-only check script + units + deploy lines; the one genuine substrate reuse is the `shift-agent-notify-owner` alert chokepoint. Report/state use trivial inline atomic writes (private files) rather than `safe_io` flock — deliberate, since `safe_io` imports `fcntl` (Linux-only) and the monitor's files have no concurrent writer.

## Drift-rule self-checks

Deployed code read before drafting this plan (CLAUDE.md drift rules — read-deployed-code-first):

- ✅ Read `tools/check-shift-agent-patch.sh` (pin gate: `_read_pin` KEY=VALUE/CRLF/quote normalization at lines 43-52; `readlink -f` + `git -c safe.directory` live-commit read at 67-70; bridge sha256 at 142; marker greps at 170-174) before drafting the runtime read logic — the monitor mirrors these reads read-only.
- ✅ Read `tools/hermes-patch-baseline.txt` (fields `HERMES_COMMIT=486b692d…`, `HERMES_VERSION=unknown`, `BRIDGE_POST_PATCH_SHA256=de178b6…`) before drafting the baseline-snapshot deploy step.
- ✅ Read `.github/workflows/hermes-drift-check.yml` (weekly upstream clone + `patch-hermes.py` dry-run + GitHub issue) before scoping — confirms the heavy patch-port detection already exists and must NOT be duplicated on the box.
- ✅ Read `src/agents/shift/scripts/shift-agent-notify-owner` (CLI `--title/--priority`, exit 0/5/6, Pushover→WhatsApp) before drafting the alert dispatch.
- ✅ Read `src/agents/shift/systemd/send-routing-accuracy-summary{,.timer,-failure}.service` (oneshot + `OnFailure=` + `ExecStartPre=/usr/bin/test -x` + hardening block) before drafting the units.
- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (script install line 38; platform `.service` install line 190; timer-enable block 730-742; smoke `test -x` list) before drafting deploy wiring.
- ✅ Read `tests/test_flyer_recovery_watchdog.py` (subprocess + `--text`/`--dry-run` + path overrides + `SHIFT_AGENT_NOTIFY_OWNER_BIN` seam + `state == before` no-mutation assertion) before drafting the test plan.
- ✅ Read `src/platform/safe_io.py` (lines 36, 83-101, 248-285) — confirmed it imports `fcntl` at module top (Linux-only; `ModuleNotFoundError` on Windows) and that callers like the flyer watchdog import it lazily in-function. Decision: monitor does **not** import `safe_io`; report/state use inline atomic `os.replace` (private files, single writer), keeping the script importable on the Windows test host and confining writes.

---

## Hermes-first analysis (checklist receipt)

`/hermes-check` run 2026-06-29 → receipt `tasks/.hermes-check-receipts/hermes-version-monitoring.json` (tag `extends-Hermes`, net-new=9, hermes-reuse=3). See the Hermes-first analysis table above.

---

## Script interface (`hermes-version-check`)

```
hermes-version-check
  --hermes-home PATH          default /root/.hermes/hermes-agent   (readlink -f resolved)
  --baseline-path PATH        default /opt/shift-agent/hermes-patch-baseline.txt
  --report-path PATH          default /opt/shift-agent/logs/hermes-version-report.json
  --state-path PATH           default /opt/shift-agent/state/hermes-version-monitor.json
  --log-path PATH             default /opt/shift-agent/logs/decisions.log
  --upstream-repo URL/PATH    default https://github.com/NousResearch/hermes-agent.git
  --upstream-timeout SECONDS  default 10
  --network-fail-alert-after N   default 3   (consecutive failures before soft network alert)
  --skip-upstream             skip the network read entirely (local-only run)
  --notify-owner-bin PATH     default /usr/local/bin/shift-agent-notify-owner
                              (env SHIFT_AGENT_NOTIFY_OWNER_BIN overrides — test seam)
  --dry-run                   compute + print report; write NOTHING, alert NOTHING
  --text                      machine-readable stdout summary (key=value tokens)
  --json                      print full report JSON to stdout
```

**Exit codes:** `0` = monitor ran and wrote its report (even when drift conditions are present — drift is *reported*, alerts go via notify-on-change). Non-zero (`EXIT_DEPENDENCY_DOWN`=6) = the monitor itself failed (could not write report/state, unhandled exception) → systemd `OnFailure` fires the failure-notify service. Mirrors the watchdog: returncode 0 with status tokens; infra-failure separate.

**Stdout `--text` tokens:** `runtime_status=<...> baseline_status=<...> bridge_status=<...> markers_status=<...> upstream_status=<...> upstream_ahead=<0|1> patch_port_review=<0|1> alert_action=<sent|suppressed|not_needed|recovery> mutation_performed=0 conditions=<comma-list>`

## Condition taxonomy (operator-specified)

**Hard (priority 1, alert on new-appearance / on-clear):**
`runtime_commit_drift`, `bridge_sha_drift`, `bridge_missing`, `patch_markers_missing`, `baseline_unreadable`, `hermes_home_unreadable`, `report_write_failed`, `state_unsafe`, `unsafe_permissions`.

**Soft (priority 0, alert on transition, throttled):**
`upstream_ahead`, `patch_port_review_required`, `upstream_check_failed` (network; throttle = notify on first transition, suppress until `consecutive_network_failures >= --network-fail-alert-after` or 24h elapsed; recovery alert on clear).

**Alert signature** = stable SHA-256 of the sorted active-condition set. Notify only when signature differs from `state.last_alert_signature`, OR a previously-active condition clears (recovery). Otherwise `alert_action=suppressed`.

## Report schema (always written; `mutation_performed` always `false`)

```json
{
  "schema_version": 1,
  "generated_at": "<iso8601>",
  "runtime_status": "match|drift|unreadable",
  "baseline_status": "ok|missing|unreadable",
  "bridge_status": "match|drift|missing",
  "patch_markers_status": "present|missing|unknown",
  "upstream_status": "ok|ahead|unknown|skipped",
  "upstream_ahead": false,
  "patch_port_review": "not_required|required",
  "pinned":  {"commit": "...", "version": "...", "bridge_sha256": "..."},
  "runtime": {"commit": "...", "bridge_sha256": "...", "markers_ok": true},
  "upstream":{"head_commit": "...", "latest_tag": "...", "reachable": true},
  "active_conditions": [],
  "alert_action": "sent|suppressed|not_needed|recovery",
  "alert_reason": "...",
  "consecutive_network_failures": 0,
  "mutation_performed": false,
  "note": "Patch-port validation is delegated to .github/workflows/hermes-drift-check.yml (weekly CI)."
}
```

## State schema (notify-on-change throttle)

```json
{
  "schema_version": 1,
  "last_alert_signature": "<sha256|empty>",
  "active_conditions": [],
  "last_notified_at": "<iso|empty>",
  "last_successful_run_at": "<iso>",
  "consecutive_network_failures": 0,
  "last_upstream_head_seen": "...",
  "last_runtime_commit_seen": "...",
  "last_bridge_sha256_seen": "..."
}
```

---

## File Structure

**Create:**
- `src/platform/scripts/hermes-version-check` — the monitor (Python, exec bit; auto-installs to `/usr/local/bin/` via deploy.sh:38). One responsibility: read live + baseline + best-effort upstream, compute conditions, write report + state, alert on-change. Pure functions for classification/signature; thin `main()` for I/O.
- `src/platform/systemd/hermes-version-check.service` — oneshot, `User=shift-agent`, security-hardened, `OnFailure=hermes-version-check-failure.service`.
- `src/platform/systemd/hermes-version-check.timer` — daily `OnCalendar`, `Persistent=true`.
- `src/platform/systemd/hermes-version-check-failure.service` — calls `shift-agent-notify-owner` on monitor crash (mirrors `send-routing-accuracy-summary-failure.service`).
- `tests/test_hermes_version_check.py` — subprocess + in-process tests (mirrors `test_flyer_recovery_watchdog.py`).
- `docs/hermes-0.17-upgrade-plan.md` — **Track B** planning-only doc (patch-port path / WhatsApp Business Cloud path). No code.

**Modify:**
- `src/agents/shift/scripts/shift-agent-deploy.sh` — (1) snapshot baseline read-only to `/opt/shift-agent/hermes-patch-baseline.txt`; (2) `install src/platform/systemd/*.timer`; (3) `systemctl enable --now hermes-version-check.timer`; (4) add `/usr/local/bin/hermes-version-check` to the installed-script smoke list.
- `src/agents/shift/scripts/shift-agent-smoke-test.sh` — assert the binary + timer presence (read-only smoke).

**Do NOT modify:** `tools/hermes-patch-baseline.txt`, `tools/check-shift-agent-patch.sh`, `tools/patch-hermes.py`, `.github/workflows/hermes-drift-check.yml`, any `src/agents/flyer/*`, any Hermes file.

---

## Tasks

### Task 1: Pure classification + signature helpers (in-process, no I/O)

**Files:** Create `src/platform/scripts/hermes-version-check` (importable: `__name__` guard so tests import helpers). Test `tests/test_hermes_version_check.py`.

**Interfaces — Produces:**
- `read_baseline(path) -> dict|None` → `{"commit","version","bridge_sha256"}` or `None` (KEY=VALUE/CRLF/quote normalization as `check-shift-agent-patch.sh:43-48`).
- `compute_conditions(local, upstream, prev_state, *, network_fail_after, now) -> {"active","hard","soft"}` (pure).
- `alert_signature(active) -> str` (sha256 of sorted set; `""` for empty).
- `decide_alert(active, hard, soft, prev_state, *, now, network_fail_after) -> {"action","reason","priority","notify"}` (pure throttle logic).

- [ ] **Step 1: Write failing tests for signature + decide_alert**

```python
import platform, sys, json, subprocess, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "platform" / "scripts" / "hermes-version-check"
import importlib.util
spec = importlib.util.spec_from_file_location("hvc", SCRIPT)
hvc = importlib.util.module_from_spec(spec); spec.loader.exec_module(hvc)

def test_signature_is_order_independent():
    assert hvc.alert_signature(["b","a"]) == hvc.alert_signature(["a","b"])
    assert hvc.alert_signature([]) == ""

def test_new_hard_condition_notifies():
    prev = {"last_alert_signature": "", "consecutive_network_failures": 0}
    d = hvc.decide_alert(["runtime_commit_drift"], ["runtime_commit_drift"], [],
                         prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is True and d["priority"] == 1

def test_repeated_same_drift_suppressed():
    sig = hvc.alert_signature(["runtime_commit_drift"])
    prev = {"last_alert_signature": sig, "consecutive_network_failures": 0}
    d = hvc.decide_alert(["runtime_commit_drift"], ["runtime_commit_drift"], [],
                         prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is False and d["action"] == "suppressed"

def test_cleared_condition_sends_recovery():
    sig = hvc.alert_signature(["runtime_commit_drift"])
    prev = {"last_alert_signature": sig, "consecutive_network_failures": 0}
    d = hvc.decide_alert([], [], [], prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is True and d["action"] == "recovery"

def test_network_failure_throttled_until_threshold():
    prev = {"last_alert_signature": "", "consecutive_network_failures": 1}
    d = hvc.decide_alert(["upstream_check_failed"], [], ["upstream_check_failed"],
                         prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is False  # 2nd failure, below threshold
```

- [ ] **Step 2: Run → FAIL** (`pytest tests/test_hermes_version_check.py -k "signature or decide or network_failure" -v`)
- [ ] **Step 3: Implement** the four pure functions. Throttle: soft `upstream_check_failed` notifies only when `consecutive_network_failures+1 >= network_fail_after` AND transition; hard notifies when signature changes with a not-yet-notified hard code; recovery when active set shrinks vs prev.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `feat(hermes-monitor): pure condition + notify-on-change throttle helpers`

### Task 2: Local runtime reads + no-mutation guarantee

**Interfaces — Produces:** `resolve_hermes_home(path)->Path`; `live_commit(home)->str|None` (`git -c safe.directory=<real> -C <home> rev-parse HEAD`, read-only); `bridge_sha(home)->str|None`; `markers_present(home)->bool|None` (grep `BEGIN shift-agent-sender-id` in run.py/whatsapp.py/bridge.js); `gather_local(home, baseline)->dict`.

- [ ] **Step 1: Failing test using a real tmp git repo as fake Hermes home**

```python
def _fake_hermes_home(tmp_path):
    home = tmp_path / "hermes"; (home / "gateway" / "platforms").mkdir(parents=True)
    (home / "scripts" / "whatsapp-bridge").mkdir(parents=True)
    body = "# BEGIN shift-agent-sender-id\n# END shift-agent-sender-id\n"
    (home / "gateway" / "run.py").write_text(body)
    (home / "gateway" / "platforms" / "whatsapp.py").write_text(body)
    (home / "scripts" / "whatsapp-bridge" / "bridge.js").write_text(body)
    for c in (["git","init","-q",str(home)],
              ["git","-C",str(home),"-c","user.email=t@t","-c","user.name=t","add","-A"],
              ["git","-C",str(home),"-c","user.email=t@t","-c","user.name=t","commit","-qm","x"]):
        subprocess.run(c, check=True)
    return home

@pytest.mark.skipif(platform.system()=="Windows", reason="git/sha perms POSIX")
def test_gather_local_matches_baseline(tmp_path):
    home = _fake_hermes_home(tmp_path)
    commit = subprocess.run(["git","-C",str(home),"rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
    baseline = {"commit": commit, "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    local = hvc.gather_local(home, baseline)
    assert local["runtime_status"]=="match" and local["bridge_status"]=="match" and local["patch_markers_status"]=="present"
```

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** read helpers (subprocess `git rev-parse`/`hashlib`/file grep; all try/except → safe `None`). **No write, fetch, checkout, or systemctl anywhere.**
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Add no-mutation proof test**

```python
@pytest.mark.skipif(platform.system()=="Windows", reason="POSIX")
def test_run_does_not_mutate_hermes_home_or_baseline(tmp_path):
    home=_fake_hermes_home(tmp_path)
    commit=subprocess.run(["git","-C",str(home),"rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
    baseline=tmp_path/"baseline.txt"
    baseline.write_text(f"HERMES_COMMIT={commit}\nHERMES_VERSION=unknown\nBRIDGE_POST_PATCH_SHA256={hvc.bridge_sha(home)}\n")
    import hashlib
    def digest(p): return hashlib.sha256("".join(sorted(str(f)+f.read_text() for f in p.rglob('*') if f.is_file() and '.git' not in f.parts)).encode()).hexdigest()
    before=digest(home); before_base=baseline.read_bytes()
    subprocess.run([sys.executable,str(SCRIPT),"--hermes-home",str(home),"--baseline-path",str(baseline),
        "--report-path",str(tmp_path/"r.json"),"--state-path",str(tmp_path/"s.json"),"--log-path",str(tmp_path/"d.log"),
        "--skip-upstream","--text"],capture_output=True,text=True,timeout=30,
        env={**os.environ,"SHIFT_AGENT_NOTIFY_OWNER_BIN":str(tmp_path/"noop")})
    after_head=subprocess.run(["git","-C",str(home),"rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
    assert digest(home)==before and after_head==commit and baseline.read_bytes()==before_base
```

- [ ] **Step 6: Commit** `feat(hermes-monitor): read-only local reads + no-mutation proof`

### Task 3: Best-effort upstream check (`git ls-remote`, fail-safe)

**Interfaces — Produces:** `upstream_check(repo, *, timeout, pinned_commit, pinned_version) -> {"status":"ok|ahead|unknown","head_commit","latest_tag","reachable","ahead"}`. Any subprocess error/timeout → `status="unknown", reachable=False` (never raises).

- [ ] **Step 1: Failing tests using a local repo + a bogus repo**

```python
@pytest.mark.skipif(platform.system()=="Windows", reason="git POSIX")
def test_upstream_ahead_detected(tmp_path):
    up=_fake_hermes_home(tmp_path/"u")
    head=subprocess.run(["git","-C",str(up),"rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
    r=hvc.upstream_check(str(up), timeout=10, pinned_commit="0"*40, pinned_version="unknown")
    assert r["status"]=="ahead" and r["head_commit"]==head and r["reachable"] is True

def test_upstream_network_failure_is_unknown(tmp_path):
    r=hvc.upstream_check(str(tmp_path/"nope.git"), timeout=2, pinned_commit="0"*40, pinned_version="unknown")
    assert r["status"]=="unknown" and r["reachable"] is False
```

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** with `subprocess.run(["git","ls-remote","--heads","--tags",repo], timeout=timeout)`; parse default-branch HEAD + highest semver tag; `ahead` iff `head_commit != pinned_commit`. Catch `TimeoutExpired`/`CalledProcessError`/`FileNotFoundError`/any → `unknown`.
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit** `feat(hermes-monitor): fail-safe git ls-remote upstream check`

### Task 4: `main()` orchestration — report, state, alert, dry-run

**Interfaces — Consumes:** Tasks 1-3. **Produces:** CLI per interface; report+state via inline `_atomic_write_json(path, obj)` (write `.tmp` → `os.replace`, mode 0640); alert via `subprocess.run([notify_bin,"--title",T,"--priority",P,msg])` with `alert_dispatched`/`alert_delivered` lines printed to stdout (§12b traceability, captured by systemd `StandardOutput`). No `safe_io`, no `decisions.log`.

- [ ] **Step 1: Failing subprocess tests (watchdog style)** — clean run (report written, no alert, `mutation_performed=false`); commit-drift alerts once then suppresses (alert file has exactly 1 line across two runs); `--dry-run` writes nothing (no report, no state, no alert).

```python
def _baseline(tmp_path, commit, sha):
    p=tmp_path/"baseline.txt"; p.write_text(f"HERMES_COMMIT={commit}\nHERMES_VERSION=unknown\nBRIDGE_POST_PATCH_SHA256={sha}\n"); return p
def _notify_capture(tmp_path):
    b=tmp_path/"notify"; out=tmp_path/"alerts.txt"
    b.write_text(f'#!/usr/bin/env python3\nimport sys,pathlib\npathlib.Path({str(out)!r}).open("a").write("|".join(sys.argv[1:])+"\\n")\n'); b.chmod(0o755)
    return b, out

@pytest.mark.skipif(platform.system()=="Windows", reason="POSIX exec bit")
def test_commit_drift_alerts_once_then_suppresses(tmp_path):
    home=_fake_hermes_home(tmp_path); baseline=_baseline(tmp_path,"0"*40,hvc.bridge_sha(home))
    notify,out=_notify_capture(tmp_path); report=tmp_path/"r.json"; state=tmp_path/"s.json"
    cmd=[sys.executable,str(SCRIPT),"--hermes-home",str(home),"--baseline-path",str(baseline),
         "--report-path",str(report),"--state-path",str(state),"--log-path",str(tmp_path/"d.log"),"--skip-upstream","--text"]
    env={**os.environ,"SHIFT_AGENT_NOTIFY_OWNER_BIN":str(notify)}
    a=subprocess.run(cmd,capture_output=True,text=True,timeout=30,env=env)
    b=subprocess.run(cmd,capture_output=True,text=True,timeout=30,env=env)
    assert "runtime_commit_drift" in a.stdout and "alert_action=sent" in a.stdout
    assert "alert_action=suppressed" in b.stdout and out.read_text().count("\n")==1
    assert json.loads(report.read_text())["mutation_performed"] is False
```

- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement** `main()`: parse → resolve home → gather_local → upstream_check (unless `--skip-upstream`) → compute_conditions vs prev → decide_alert → (unless dry-run) atomic-write report+state, dispatch alert with dispatched/delivered audit pair, append `hermes_version_monitor_checked` → print `--text`. Report/state write failure → set `report_write_failed`/`state_unsafe`, best-effort alert, exit 6. Whole body wrapped → any exception → stderr + exit 6 (OnFailure catches).
- [ ] **Step 4: Run → PASS**
- [ ] **Step 5:** Add `unsafe_permissions` POSIX test (pre-create `state` `0o666` → assert `unsafe_permissions` + hard alert).
- [ ] **Step 6: Commit** `feat(hermes-monitor): main orchestration, report+state+alert, dry-run`

### Task 5: systemd units (service + timer + failure-notify)

- [ ] **Step 1: `hermes-version-check.service`**

```ini
[Unit]
Description=Hermes version monitor (read-only) — runtime pin vs baseline + best-effort upstream
After=network-online.target
Wants=network-online.target
OnFailure=hermes-version-check-failure.service

[Service]
Type=oneshot
User=shift-agent
Group=shift-agent
EnvironmentFile=/opt/shift-agent/.env
Environment=HOME=/opt/shift-agent
ExecStartPre=/usr/bin/test -x /usr/local/bin/hermes-version-check
ExecStartPre=/usr/bin/test -x /usr/local/bin/shift-agent-notify-owner
ExecStart=/usr/local/bin/hermes-version-check --text
StandardOutput=append:/opt/shift-agent/logs/hermes-version-check.log
StandardError=append:/opt/shift-agent/logs/hermes-version-check.log
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/shift-agent
ProtectHome=read-only
PrivateTmp=true
RuntimeDirectory=shift-agent

[Install]
WantedBy=multi-user.target
```

> `ProtectSystem=strict` + `ReadWritePaths=/opt/shift-agent` structurally guarantee the monitor cannot mutate Hermes/gateway/baseline. `/root/.hermes` resolves (readlink -f) to `/usr/local/lib/hermes-agent` (readable). §9a: verify shift-agent read access on the box at validation time (precedent `check-shift-agent-patch.sh:68` `sudo -u shift-agent git`).

- [ ] **Step 2: `hermes-version-check.timer`**

```ini
[Unit]
Description=Daily Hermes version monitor

[Timer]
OnCalendar=*-*-* 08:30:00
Persistent=true
AccuracySec=5min
Unit=hermes-version-check.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: `hermes-version-check-failure.service`** (mirror routing-summary failure handler; `--priority 1`, plain text, no recursive OnFailure).
- [ ] **Step 4: Commit** `feat(hermes-monitor): systemd service + daily timer + failure-notify`

### Task 6: Static guard tests (boundary enforcement)

- [ ] **Step 1: Static tests reading the SHIPPED files**

```python
SERVICE = REPO/"src"/"platform"/"systemd"/"hermes-version-check.service"

def test_script_has_no_mutation_verbs():
    txt = SCRIPT.read_text()
    for bad in ["clone","fetch","checkout","git pull","git reset","systemctl","patch-hermes"]:
        assert bad not in txt, bad
    assert "ls-remote" in txt  # the one allowed upstream read

def test_service_is_read_only_and_wires_onfailure():
    s = SERVICE.read_text()
    assert "OnFailure=hermes-version-check-failure.service" in s
    assert "ProtectSystem=strict" in s and "ReadWritePaths=/opt/shift-agent" in s
    assert "User=shift-agent" in s and "ExecStartPost" not in s and "systemctl" not in s
```

- [ ] **Step 2: Run → adjust to match implementation → PASS**
- [ ] **Step 3: Commit** `test(hermes-monitor): static boundary guards`

### Task 7: Deploy wiring (idempotent, additive)

- [ ] **Step 1:** In `install_artifacts()` near platform installs (~line 94), add read-only baseline snapshot:

```bash
    # Hermes version monitor needs the pinned baseline at a stable runtime path
    # (read-only snapshot; the monitor never writes it).
    [ -f tools/hermes-patch-baseline.txt ] && \
        install -m 644 tools/hermes-patch-baseline.txt /opt/shift-agent/hermes-patch-baseline.txt
```

- [ ] **Step 2:** After line 190 (`install ... src/platform/systemd/*.service`), add platform timer install:

```bash
    install -m 644 src/platform/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
```

- [ ] **Step 3:** In the timer-enable block (~line 732), add (idempotent, unconditional — Hermes exists on every VPS):

```bash
    systemctl enable --now hermes-version-check.timer 2>/dev/null || true
```

- [ ] **Step 4:** Add `/usr/local/bin/hermes-version-check` to the installed-script smoke `test -x` list + `shift-agent-smoke-test.sh` (read-only presence).
- [ ] **Step 5: Static test**

```python
def test_deploy_wires_monitor_idempotently():
    d=(REPO/"src"/"agents"/"shift"/"scripts"/"shift-agent-deploy.sh").read_text()
    assert "enable --now hermes-version-check.timer" in d
    assert "install -m 644 src/platform/systemd/*.timer" in d
    assert "install -m 644 tools/hermes-patch-baseline.txt /opt/shift-agent/hermes-patch-baseline.txt" in d
```

- [ ] **Step 6: Commit** `feat(hermes-monitor): idempotent deploy wiring + smoke presence`

### Task 8: Track B — Hermes 0.17 upgrade plan doc (planning-only)

- [ ] **Step 1:** Create `docs/hermes-0.17-upgrade-plan.md`: current pin (0.14, `486b692d`), why 0.17 is blocked (patch anchors gone — `whatsapp.py` 404, `run.py` refactor; ref MEMORY `project_hermes_update_blocked_patch_port`), two forward paths (A: port `patch-hermes.py` anchors to 0.17 tree; B: official 0.17 WhatsApp Business Cloud API + retire bridge patches), decision gates, explicit "NOT started — planning only." Cross-link CI drift workflow + this monitor as the trigger signals.
- [ ] **Step 2: Commit** `docs(hermes): Track B 0.17 upgrade plan (planning-only)`

### Task 9: Full suite + evidence capture

- [ ] **Step 1:** `python -m pytest tests/test_hermes_version_check.py -v` → all pass.
- [ ] **Step 2:** Regression sample: `pytest tests/ -q -k "deploy or smoke or notify"`.
- [ ] **Step 3:** Capture evidence: dry-run output, manual run output, report sample (JSON), `--text` tokens, no-mutation test output. On-box `systemctl` status + before/after gateway proof = **operator-gated validation** (PR does not deploy).
- [ ] **Step 4: Commit** plan + evidence.

---

## Self-Review (spec coverage)

- available updates → Task 3 (`upstream_ahead`/`latest_tag`) ✓
- patch-port requirements → Task 3 (`patch_port_review=required`; heavy detection delegated to CI) ✓
- pinned/runtime unchanged → Task 2 ✓
- systemd service/timer + failure notify → Task 5 ✓
- deploy wiring → Task 7 ✓
- static/runtime tests → Tasks 1-4, 6, 7 ✓
- Track B doc → Task 8 ✓
- notify-on-change + network fail-safe → Tasks 1, 4 ✓
- no runtime mutation / gateway / baseline / skills / auto-update → Global Constraints + Task 2 proof + Task 6 guards + Task 5 `ProtectSystem=strict` ✓
- log/report ownership + permissions → `User=shift-agent` + `unsafe_permissions` condition ✓
- deploy idempotency → `enable --now` + `install ... || true` + Task 7 test ✓

## Evidence to attach to the PR

1. `pytest tests/test_hermes_version_check.py -v` (all green)
2. Dry-run output (`--dry-run --text`) — report computed, nothing written
3. Manual run output (`--text` tokens) on a fixture
4. Generated report sample (`hermes-version-report.json`)
5. No-mutation test output (`test_run_does_not_mutate_hermes_home_or_baseline`)
6. systemd timer status — operator-gated (PR does not deploy); unit-file review + structural argument provided
7. Gateway commit/baseline unchanged — proven by no-mutation test + `ProtectSystem=strict`; on-box before/after deferred to operator validation
8. Rollback plan (below)

## Rollback plan

The PR is dormant until deployed; even after deploy it is read-only. Disable post-deploy: `systemctl disable --now hermes-version-check.timer` (one command). Full revert: `git revert` the PR + redeploy — removes timer install + enable lines; the read-only baseline snapshot + script are harmless if left. No state migration, no data to clean (report/state files disposable under `/opt/shift-agent/`).

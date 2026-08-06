# PRODUCTION ACCESS-CONTROL VERIFICATION — main-vps

**Drift-check tag:** `extends-Hermes`

**Scope:** `GATEWAY_ALLOW_ALL_USERS` · `COCKPIT_AUTH_BYPASS` · `COCKPIT_AUTH_BYPASS_ALLOW_PROD`
**Mode:** read-only investigation. **No config mutation, no service restart, no live customer
message, no destructive or state-mutating request.** Only `GET` was issued, and only to
`/`, `/api/`, `/api/health`, `/api/docs`, `/api/openapi.json`, `/api/roster`.
**Host:** `46.62.206.192`. **Evidence timestamp:** 2026-08-01T23:1x–23:3xZ.
**Routed separately from** the Cloud API thread and from
`tasks/fleet-escalation-hermes-pin-deploy-gate.md`.

## Verdict up front

| Finding | Raised as | **Verified severity** |
|---|---|---|
| A — `GATEWAY_ALLOW_ALL_USERS=true` | HIGH pending containment proof | **LOW / inert for this deployment** — no WhatsApp code path consumes it (§A) |
| B — cockpit bypass flags | potential HIGH | **HIGH configuration debt + information disclosure, externally exposed.** NOT an unauthenticated-compromise incident: primary auth holds (§B) |

Neither is the incident it appeared to be. Both were worth checking; §D records why the
appearance and the reality diverged.

> **CONTAINMENT EXECUTED 2026-08-06 — Finding B is CLOSED.** nginx moved to a loopback bind, both
> bypass variables removed, and the deployed fail-closed guard restored from the exact deployed
> release `d01c88a`. Port 8080, `/api/docs` and `/api/openapi.json` are no longer reachable from the
> internet, and production bypass can no longer be re-enabled by any environment variable. Full
> evidence in **§H**. Finding A (`GATEWAY_ALLOW_ALL_USERS`, inert) remains fleet-owned and untouched.

---

## New primitives introduced

None. Read-only verification of existing production configuration.

## Hermes-first capability checklist

| # | Step | Tag | Basis |
|---|---|---|---|
| 1 | Enumerate runtime-effective env per consuming process | `[Hermes]`-adjacent | `/proc/<pid>/environ`, standard tooling |
| 2 | Identify consuming code paths | `[Hermes]` | grep of the installed tree; no new tooling |
| 3 | Determine external reachability | `[net-new]` (one-off) | `ss -ltnp` + external `GET`; not a durable capability |
| 4 | Classify severity against the decision rule | `[net-new]` (judgement) | reviewer-supplied rule |

## Drift-rule self-checks

- ✅ Read `src/platform/systemd/hermes-gateway.service` — established that the gateway loads
  `EnvironmentFile=/opt/shift-agent/.env`, which is why env had to be read **per process**
  rather than per file (§B.1 — the file-vs-process distinction is what made this finding
  tractable).
- ✅ Read `src/platform/safe_io.py` — confirmed the app-side outbound chokepoint is unrelated to
  cockpit auth; the two controls do not compensate for each other.
- ✅ Read `src/platform/scripts/validate-sender-block` — confirmed WhatsApp admission is
  sender-block/role driven, which is what makes Finding A inert (§A.2).
- ✅ Read `tools/check-shift-agent-patch.sh` — confirms the fail-closed pattern this codebase
  uses, the same pattern `_forbid_prod_bypass` implements and that `ALLOW_PROD` suppresses.

---

## A. `GATEWAY_ALLOW_ALL_USERS=true` — inert for the active platform

### A.1 Runtime-effective value

Set to `true` in both `/root/.hermes/.env` and `/opt/shift-agent/.env`, and present in the
gateway process environment (PID 765505).

### A.2 Consuming code paths — WhatsApp is NOT among them

Every consumer in the installed v0.19.1 tree:

| File | Role |
|---|---|
| `plugins/platforms/discord/adapter.py:4489,4566,8015` | Discord admission |
| `plugins/platforms/wecom/adapter.py:872,1820` | WeCom admission |
| `plugins/platforms/feishu/adapter.py:4327` | Feishu admission |
| `hermes_cli/config_defaults.py:4066`, `hermes_cli/web_server.py:7961`, `tools/environments/local.py:540` | config plumbing / declaration |
| `hermes_cli/gateway.py:5625` | writes the value during setup |

**Neither `plugins/platforms/whatsapp/adapter.py` nor `gateway/platforms/whatsapp_cloud.py`
reads it.** WhatsApp admission is governed by `WHATSAPP_ALLOWED_USERS` (confirmed present in the
gateway process environment) plus the sender-block/`identify-sender` role gates.

**Answers to the required proof list:** it disables per-platform sender allowlisting for
Discord/WeCom/Feishu only; it does **not** affect WhatsApp; unknown WhatsApp senders cannot
reach the model or tools through this flag; `identify-sender`, dispatcher role gates, and the
outbound screen are unaffected by it.

### A.3 Residual work (fleet session)

Confirm no Discord/WeCom/Feishu platform is enabled. If none is, the flag is inert and should be
removed as misleading configuration rather than left to imply a permissive posture.
**Do not toggle it** without that confirmation — per the reviewer, a rushed change could alter
routing or mask the real boundary.

---

## B. Cockpit bypass — real control weakening, but primary auth holds

### B.1 Runtime-effective values, read from the consuming process

`shift-agent-cockpit.service` → `uvicorn app.main:app --host 127.0.0.1 --port 8081`,
PID 749749, `EnvironmentFile=/opt/shift-agent/.env`:

```
COCKPIT_AUTH_BYPASS=true
COCKPIT_AUTH_BYPASS_ALLOW_PROD=true
```

Both confirmed in `/proc/749749/environ` — i.e. genuinely effective for the process that
consumes them, not merely present in a file.

### B.2 What the flags actually do — the semantics are inverted from the obvious reading

`app/config.py:150-151`:

```python
if self.auth_bypass_enabled and not _env_flag("COCKPIT_AUTH_BYPASS_ALLOW_PROD"):
    _forbid_prod_bypass("COCKPIT_AUTH_BYPASS", "skip cockpit authentication and OTP checks")
```

`_forbid_prod_bypass` (`config.py:37-43`) raises `RuntimeError` — refusing startup — when
`/opt/shift-agent` exists and no pytest run is active.

> **`COCKPIT_AUTH_BYPASS_ALLOW_PROD=true` does not enable anything. It suppresses the guard
> that would otherwise refuse to boot the cockpit in production with the bypass on.** A
> deliberate override of a fail-closed protection.

### B.3 Blast radius — narrower than "authentication bypassed"

`auth.py:286` `require_fresh_otp` calls `require_auth` **first**, then:

```python
claims = await require_auth(request)
if settings.auth_bypass_enabled:
    return claims          # <-- skips the ≤5-minute freshness check only
```

So:

| Control | Status under the flags |
|---|---|
| Primary session authentication (`require_auth`) | **ENFORCED** — never consults the flag |
| Step-up freshness (JWT issued ≤5 min) for sensitive actions | **DISABLED** |
| `require_fresh_pushover_otp` method check (`auth_method == "pushover"`) | **ENFORCED** — survives at `auth.py:302+` |
| Startup guard against prod bypass | **SUPPRESSED** by `ALLOW_PROD` |

**Net effect:** a session token older than 5 minutes can perform medium-sensitivity actions
(most config `PATCH`es, WhatsApp unlink) and self-recovery-prevention actions without
re-verifying — though the latter still require the session to have been established via
Pushover. The docstring at `auth.py:302` states these gates exist to stop a TOTP-only
compromise from disabling TOTP, re-enrolling, or swapping Pushover keys; the *freshness* half of
that defence is currently off.

**Empirical confirmation:** unauthenticated `GET /api/roster` → **HTTP 401**. Data endpoints are
not open.

### B.4 Exposure — externally reachable, no network control

| Layer | State |
|---|---|
| nginx `shift-agent-cockpit` | `listen 8080`, **bound `0.0.0.0`** |
| Auth at proxy | **none** — no `auth_basic`, `allow`/`deny`, or `auth_request` anywhere in `/etc/nginx/` |
| Backend | uvicorn `127.0.0.1:8081`, proxied via `location /api/` |
| ufw | **inactive** |
| iptables INPUT | policy `ACCEPT`, only inert ufw chains |

Unauthenticated external `GET` results (from off-host):

| Path | Result |
|---|---|
| `/` | 200 — cockpit SPA |
| `/api/health` | 200 |
| `/api/docs` | **200 — Swagger UI publicly readable** |
| `/api/openapi.json` | **200 — full schema publicly readable** |
| `/api/roster` | 401 — auth enforced |

The public schema describes **65 paths / 41 mutating operations**, including
`PATCH /config/sensitive`, `DELETE /roster/employee/{id}`, `DELETE /schedule/{date}`,
`POST /flyer/campaigns/send`, `POST /commerce/orders/{id}/transition`.
`securitySchemes: None`, global `security: None`. **No mutating endpoint was called.**

### B.5 Severity against the reviewer's decision rule

Neither branch fits exactly. Primary auth holds, so this is not "bypass active and remotely
reachable → immediate incident" in the unauthenticated-compromise sense. But it is not
"loopback-only and unreachable externally" either — it **is** externally reachable.

**Classification: HIGH configuration debt + information disclosure, already exposed.**
Not a confirmed compromise; a materially weakened control on an internet-facing surface.

### B.6 Recommended remediation — fleet/security session, not executed here

1. Remove `COCKPIT_AUTH_BYPASS_ALLOW_PROD` and `COCKPIT_AUTH_BYPASS` from `/opt/shift-agent/.env`
   so `_forbid_prod_bypass` governs again. **Expect a restart to be required — coordinate with
   the open change window.**
2. Bind nginx to `127.0.0.1:8080` (access via SSH tunnel), or enable ufw allowing only 22 —
   removes the internet surface independent of (1).
3. Disable `/api/docs` and `/api/openapi.json` in production, or place them behind auth.
4. Re-run the external `GET` matrix afterwards as closure evidence.

Items 2 and 3 are independent of the flags and reduce exposure even if the bypass is
intentional.

### B.7 Explicitly NOT verified

- Whether an external party can **obtain** a valid session (auth-flow strength). Testing this
  would mean attempting authentication — not done.
- The full route list gated by `require_fresh_otp` vs `require_auth`.
- Whether any route omits `require_auth` beyond `/health` and the docs endpoints.
- JWT secret strength in practice (a 64+ hex-char validator exists at `config.py:146-149`).

---

## C. Answers to the required return format

```
runtime-effective boolean values:
  GATEWAY_ALLOW_ALL_USERS=true  (inert for WhatsApp — §A.2)
  COCKPIT_AUTH_BYPASS=true      (skips OTP freshness only — §B.3)
  COCKPIT_AUTH_BYPASS_ALLOW_PROD=true  (suppresses the startup guard — §B.2)

consuming code paths:
  A: discord/wecom/feishu adapters + config plumbing; NO WhatsApp consumer
  B: config.py:111,150 · auth.py:294 (inside require_fresh_otp) · routers/auth.py:72,136

exposed interfaces and ports:
  nginx 0.0.0.0:8080 (no auth) -> uvicorn 127.0.0.1:8081 ; bridge 127.0.0.1:3000 ; sshd 0.0.0.0:22
  ufw inactive; iptables INPUT ACCEPT

effective authentication chain:
  nginx (none) -> require_auth (ENFORCED) -> require_fresh_otp (freshness BYPASSED)
                                          -> require_fresh_pushover_otp (method check ENFORCED)

unauthorized-user reachability result:
  static SPA, /api/health, /api/docs, /api/openapi.json  -> reachable unauthenticated
  /api/roster (data)                                     -> 401

severity: A = LOW/inert · B = HIGH configuration debt + information disclosure, externally
          exposed; not a confirmed unauthenticated compromise
remediation: §B.6
```

## D. Route-authorization inventory (required closure evidence)

Method: AST static inspection of the deployed `app/routers/*.py` (pulled read-only; the app was
never imported or executed), cross-checked against the live OpenAPI schema, then verified with
**non-destructive unauthenticated `GET` requests only. No mutating endpoint was called.**

Auth model verified in `app/auth.py`: `require_fresh_pushover_otp` → `require_fresh_otp` →
`require_auth`, so any of the three implies primary authentication. Auth is applied exclusively
via `Depends(...)` in handler signatures — there are **no** app-level or router-level
dependencies, so a route with no auth dependency is public by construction.

### D.1 Totals

| Metric | Count |
|---|---|
| Route operations discovered (static) | **68** |
| Operations in live OpenAPI schema | **68** (27 GET + 35 POST + 3 PATCH + 2 DELETE + 1 PUT) — **exact match, inventory complete** |
| Authenticated | 62 |
| Public (no auth dependency) | 6 |
| Mutating | 41 |
| Mutating **and** public | 4 — all login-shell (§D.2) |
| Mutating, authenticated, **no** fresh-OTP tier by design | 18 |
| Mutating, authenticated, **with** fresh-OTP tier | 19 — **all currently downgraded by the bypass** |

Machine-readable inventory: `route_inventory.json` (path, method, authenticated, require_auth,
require_fresh_otp, require_fresh_pushover_otp, mutating, file, handler).

### D.2 The 6 public routes — all health or login shell

| Method | Path | Handler |
|---|---|---|
| GET | `/health` | `health.py::health_public` |
| GET | `/auth/status` | `auth.py::auth_status` |
| POST | `/auth/request-otp` | `auth.py::request_otp` |
| POST | `/auth/verify-otp` | `auth.py::verify` |
| POST | `/auth/verify-totp` | `auth.py::verify_totp_route` |
| POST | `/auth/logout` | `auth.py::logout` |

**No data endpoint is public.** The four public mutating routes are the login flow, which must
be reachable pre-authentication. (Rate-limiting on the OTP verify routes is a separate control
not assessed here.)

### D.3 Runtime verification — static matches runtime 20/20

All 20 GET routes without path parameters were probed unauthenticated from off-host:

- **18/18** routes the static analysis marked authenticated returned **HTTP 401** — `/audit`,
  `/auth/me`, `/commerce/orders`, `/config`, `/dashboard`, `/decisions`, `/decisions.csv`,
  `/disclosures`, `/flyer/{customers,guest-orders,health,manual-queue,projects,summary}`,
  `/pending`, `/roster`, `/schedule`, `/whatsapp/status`.
- **2/2** intended-public routes returned **HTTP 200** — `/health`, `/auth/status`.

No mismatch. Primary authentication is genuinely enforced on every data endpoint.

### D.4 Invariant results

| # | Required invariant | Result |
|---|---|---|
| 1 | Only approved endpoints (health / login shell) may be public | **PASS** — all 6 public routes are exactly that (§D.2) |
| 2 | Swagger + OpenAPI must not be publicly accessible in production | **FAIL** — `/api/docs` and `/api/openapi.json` both return 200 externally |
| 3 | Every data endpoint requires primary authentication | **PASS** — 18/18 verified 401 (§D.3) |
| 4 | Every sensitive/mutating operation requires the intended fresh-OTP policy | **FAIL (currently)** — the policy is correctly *declared* on 19 mutating routes, but `COCKPIT_AUTH_BYPASS` neutralizes all 19 at runtime |
| 5 | No route silently depends only on frontend controls | **PASS for GET** (verified). For mutating routes, static analysis shows dependencies present; not runtime-tested because mutating calls are prohibited |
| 6 | No production bypass flag can disable these protections without failing startup | **FAIL** — `COCKPIT_AUTH_BYPASS_ALLOW_PROD=true` suppresses `_forbid_prod_bypass` (`config.py:37-43,150-151`) |

Three PASS, three FAIL. The three failures map exactly onto the authorized containment actions;
no additional remediation is implied by this inventory.

### D.5 Separate design observation — not caused by the bypass

18 mutating routes carry **no fresh-OTP tier by design**, including several that look
step-up-worthy:

`POST /safety/disable` · `POST /safety/enable` (agent kill switch) · `PATCH /config` ·
`POST /roster/employee` · `PATCH /roster/employee/{id}` · `DELETE /roster/employee/{id}` ·
`PUT /schedule/{date}` · `DELETE /schedule/{date}` · `POST /pending/{id}/cancel`

These are protected by primary authentication only **even when the bypass is removed**. That is
a design question for the operator, independent of this incident — flagged, not actioned.
Note `PATCH /config/sensitive` *does* carry a fresh tier, so the tiering was deliberate.

### D.6 Post-containment re-verification script

After containment, re-run in this order as closure evidence:

1. External `GET :8080/` → expect connection refused/filtered, or 200 only from the authorized source.
2. External `GET /api/docs` and `/api/openapi.json` → expect denied.
3. External `GET` on the 18 protected routes in §D.3 → expect 401/403 (unchanged).
4. `GET /health` → matches the explicit exposure policy.
5. Operator login through the intended administrative path → still succeeds.
6. `tr '\0' '\n' < /proc/<cockpit-pid>/environ | grep COCKPIT_AUTH_BYPASS` → expect **no output**.

Step 6 is the one that proves the flags are gone from the *process*, not merely from a file —
the distinction that produced this finding in the first place (§B.1).

## G. DEFERRED — authorization-tier review for the 18 non-step-up mutating routes

**Not part of this incident and must not delay containment.** Recorded here so the rulings
already issued are not lost. **Do not call these routes to validate authentication.**

Rulings issued 2026-08-01:

- `POST /safety/disable` — **may reasonably remain primary-auth-only.** An emergency stop should
  stay quickly reachable.
- `POST /safety/enable` — **should presumptively require fresh OTP.** It restores production
  activity, so it is the asymmetric-risk half of the pair.
- Configuration, roster, and schedule mutations — route-by-route review on blast radius,
  reversibility, and downstream automation.
- General principle: any route that can **enable sends, change credentials, weaken safety
  controls, or alter broad production behavior** should require fresh OTP.

Table schema for the review, to be produced statically:

```
route | current tier | mutation effect | blast radius | reversible | audit receipt |
recommended tier | reason
```

Candidate set (the 18 from §D.5): `POST /safety/{disable,enable}` · `PATCH /config` ·
`POST /roster/employee` · `PATCH /roster/employee/{id}` · `DELETE /roster/employee/{id}` ·
`PUT /schedule/{date}` · `DELETE /schedule/{date}` · `POST /pending/{id}/cancel` ·
`POST /auth/totp/enroll-verify` · `POST /flyer/campaigns/{preview,preview-csv}` ·
`POST /flyer/manual-queue/{claim-next,{id}/assign,{id}/claim,{id}/unclaim}` ·
`POST /whatsapp/repair/{sid}/cancel`.

Note for whoever picks this up: `POST /flyer/campaigns/send` and `send-csv` already carry a
fresh tier — the *preview* variants are the ones in this set, so check whether preview can
trigger any outbound effect before assigning a tier.

## E. Precise incident wording (for the record)

> **Confirmed public information exposure and production step-up-authentication bypass; no
> confirmed primary-authentication bypass or unauthorized data access.**

## F. Why the appearance and the reality diverged — process note

Both findings looked more severe than they are, for two distinct reasons worth recording:

1. **Finding A** — the variable *name* implies gateway-wide permissiveness. Only tracing
   consumers showed it never reaches WhatsApp.
2. **Finding B** — I first read the flags from `.env` files and reasoned about them; the flags
   are real, but the effect depends on **which process** loads them and **where the value is
   consumed**. Reading `/proc/<pid>/environ` and then `auth.py` showed the bypass covers OTP
   freshness, not primary authentication — consistent with the observed 401.

Both are instances of the same discipline: a configuration value's name and presence do not
establish its effect; only the consuming code path on the runtime-loaded process does. That is
the same failure mode as the earlier `whatsapp.py` screening scare in
`tasks/whatsapp-cloud-api-baseline-reconciliation.md` §0.

---

## H. CONTAINMENT EXECUTED — 2026-08-06

**Mode:** mutating. Authorized scope only: Cockpit fail-closed guard restoration, removal of the two
Cockpit bypass variables, nginx loopback binding, Cockpit/nginx restart, non-destructive
verification. **No mutating endpoint was called at any point** — every request issued was a `GET`.

### H.0 Pre-mutation blockers resolved first

**Concurrency.** A foreign worktree `.config/superpowers/worktrees/SME-Agents/cockpit-temp-auth-bypass`
holds **uncommitted** edits to `web/backend/app/config.py` and `web/backend/tests/test_auth.py` that
*are* the bypass mechanism. Last modified **2026-06-03**, ~9 weeks stale, not locked, HEAD on an old
June commit — no active session. It was **not modified, committed, reset or cleaned.** Its
disposition is a separate owner decision; it is the provenance record of how production came to run
uncommitted code.

**Root cause (new — not established by the 2026-08-01 read-only pass).** The deployed Cockpit was
running code that exists in **no committed state**. Deployed `config.py:150` read:

```python
if self.auth_bypass_enabled and not _env_flag("COCKPIT_AUTH_BYPASS_ALLOW_PROD"):
```

`COCKPIT_AUTH_BYPASS_ALLOW_PROD` exists nowhere on `origin/main`, nor at the deployed release
`d01c88a`. The deployed file's mtime (Jun 3 00:38) matches the worktree edit (Jun 3 00:36).
Consequence: **environment-variable removal alone could not satisfy the required invariant** — the
build itself was written to honor the override, so the hole would have stayed one `.env` line away
from reopening.

### H.1 Implementation source

Tracked `web/backend/app/config.py` at the **exact deployed release commit `d01c88a`** (verified an
ancestor of `origin/main`) carries the unconditional guard and no escape hatch. Diff between tracked
`d01c88a` and the deployed file was **exactly 9 lines / 3 hunks**, all escape-hatch, nothing else —
so restoration is minimal and pulls in **no unrelated current-main changes**. No hotfix was needed.

### H.2 Files changed, before/after hashes

Complete SHA-256 values, untruncated:

**`/opt/shift-agent/cockpit/backend/app/config.py`**
```
before: 3f2ed791b5d6acefd80da422af161f75d850e5783b189df773ac42a1c50adcd6
after : 73aa55ab59431e94f463e6d3446ecc98a16f786d6e1f8e76c6c3b8e0921be14b
```
The `after` value is byte-identical to tracked `d01c88a:web/backend/app/config.py`.

**`/etc/nginx/sites-available/shift-agent-cockpit`**
```
before: 48b479e0bc2e323474a87081da4091a6e9d801da9d241fdf9f8e68129a10b4fb
after : d632e27d93a2b5fb3e4d80f8df913d135572050437df3c0af43808cd4c762357
```

**`/root/.hermes/.env`**
```
before: 59c41359a2059b8e05deb5f85879f62ad40691ff54ec63b4a3f85065d2716a63
after : 2100e6f79e97fedfe153245d2d76af6590d4012c710941c6cdfdfb7edccd4604
```

Backups: `/root/cockpit-containment-backups/{config.py,nginx-shift-agent-cockpit,hermes.env}.20260806-150453`

### H.2b The escape-hatch diff, verbatim

Tracked `d01c88a` (left) versus what was deployed (right) — nine changed lines, three hunks, all
escape-hatch, nothing else:

```diff
+def _env_flag(name: str) -> bool:
+    return os.environ.get(name, "false").lower() in ("1", "true", "yes")
+
+
 def _forbid_prod_bypass(flag_name: str, reason: str) -> None:
@@
-    auth_bypass_enabled: bool = Field(
-        default_factory=lambda: os.environ.get("COCKPIT_AUTH_BYPASS", "false").lower()
-        in ("1", "true", "yes")
-    )
+    auth_bypass_enabled: bool = Field(default_factory=lambda: _env_flag("COCKPIT_AUTH_BYPASS"))
@@
-        if self.auth_bypass_enabled:
+        if self.auth_bypass_enabled and not _env_flag("COCKPIT_AUTH_BYPASS_ALLOW_PROD"):
             _forbid_prod_bypass("COCKPIT_AUTH_BYPASS", "skip cockpit authentication and OTP checks")
```

### H.3 Environment edit

`/opt/shift-agent/.env` is a **symlink** to `/root/.hermes/.env`; `sed -i` was **not** used against
the symlink path. The real target was edited atomically (`grep -v` → temp → `chown/chmod --reference`
→ `mv`). Symlink intact, ownership/permissions preserved (`shift-agent:shift-agent 600`). `diff`
against the backup shows **exactly two removed lines** (70 → 68):

```
< COCKPIT_AUTH_BYPASS=true
< COCKPIT_AUTH_BYPASS_ALLOW_PROD=true
```

### H.4 Listener change

`listen 8080;` → `listen 127.0.0.1:8080;`. `nginx -t` passed before any reload.

**Verification note worth keeping:** `systemctl reload nginx` was **not sufficient** — a reload
re-reads config but inherits existing listening sockets, so `0.0.0.0:8080` persisted with unchanged
PIDs even though the config was correct and `nginx -t` passed. A full `systemctl restart nginx` was
required to rebind. Config correctness is not bind correctness; verify the socket, not the file.

| | before | after |
|---|---|---|
| nginx | `0.0.0.0:8080` (pids 957648/650/651) | `127.0.0.1:8080` (pids 1030740/741/742) |
| uvicorn | `127.0.0.1:8081` | `127.0.0.1:8081` (unchanged, pid 1030619) |

### H.5 Proof of containment

**Process environment** — `/proc/1030619/environ`: both `COCKPIT_AUTH_BYPASS` and
`COCKPIT_AUTH_BYPASS_ALLOW_PROD` **absent**.

**Startup-refusal invariant** — with *both* variables forced on, against the deployed code:

```
PASS: startup REFUSED -> RuntimeError
  COCKPIT_AUTH_BYPASS=1 with /opt/shift-agent present is forbidden outside an
  active pytest run (PYTEST_CURRENT_TEST must be set)...
```

`ALLOW_PROD` no longer rescues it. **No environment variable can now start Cockpit in production
with the bypass enabled.**

**Off-host external (public internet, unauthenticated)** — all refused, `curl rc=7`:

| path | before | after |
|---|---|---|
| `/` | 200 | **UNREACHABLE** |
| `/api/health` | 200 | **UNREACHABLE** |
| `/api/docs` | **200 (exposed)** | **UNREACHABLE** |
| `/api/openapi.json` | **200 (exposed)** | **UNREACHABLE** |
| `/api/roster` | 401 | **UNREACHABLE** |

**Authorized admin path** — `ssh -L 8080:127.0.0.1:8080 main-vps` → `http://localhost:8080`:
`/` 200 · `/api/health` 200 · `/api/docs` 200 · **`/api/roster` 401** (representative protected GET,
unauthenticated, through the authorized path). Tunnel closed afterwards; `localhost:8080` returns to
unreachable.

### H.5b Operator access path — verified reachable and correctly gated

Through the loopback tunnel, unauthenticated:

| check | result |
|---|---|
| `GET /` (login shell loads) | 200 |
| `GET /api/health` | 200 |
| `GET /api/decisions.csv` — the **one** non-mutating fresh-OTP-protected route (`require_fresh_otp`, PII export, `routers/decisions.py:72-78`) | **401** |
| routes registered after restart | 65, including all seven `/auth/*` routes and `/decisions.csv` |

The auth stack loaded cleanly after the restart; the fresh-OTP gate is wired and reachable.

**Login capability confirmed configured, not exercised.** TOTP is **not enrolled**
(`cockpit-totp-secret.json`, `-pending.json`, `-failures.json` all absent), so
`POST /auth/verify-totp` — a public fallback that mints a session on its own — is unavailable.
The sole login path is Pushover OTP. Its credentials are **present and well-formed**
(`config.yaml` → `alerting.pushover_app_token` and `alerting.pushover_user_key`, 30 chars each;
`owner.phone` set). They are absent from the process environment because the cockpit reads them
from `config.yaml`, not env — the same file-versus-process distinction recorded in §B.1.

**No lockout was introduced** — confirmed empirically in §H.5c, where the owner completed a real
login.

### H.5c Operator authentication — EXECUTED 2026-08-06, owner-in-the-loop

The full chain was performed by the owner through the loopback tunnel. **The OTP was entered
directly into the Cockpit form and was never disclosed to, recorded by, or transmitted through the
session.** Verification used only server-side status codes and audit event names; no response body
was read, no configuration changed, no service restarted, no other identity tested, no TOTP
enrolment attempted, and no mutating business endpoint called.

| time (UTC) | event / request | result |
|---|---|---|
| 15:37:43 | `GET /api/auth/me` — before login | **401** |
| 15:42:50 | `auth.otp.verify_success` | login succeeds |
| 15:42:51 | `GET /api/auth/me` — after login | **200** |
| 15:43:10 | `GET /api/roster` — representative authenticated GET | **200** |
| 15:44:46 | `GET /api/decisions.csv` — fresh-OTP tier, **+1m56s** after login | **200** |
| 15:44:46 | `decisions.csv_export` audit entry | export audited |
| 15:45:09 | `POST /api/auth/logout` | **200** |
| 15:45:10 | `GET /api/auth/me` — after logout | **401** |
| 15:45:21 | `auth.otp.verify_success` — second login | login succeeds |
| 15:45:32 | `GET /api/roster` | **200** |
| 15:46:21 | `POST /api/auth/logout` | **200** |
| 15:46:21 | `GET /api/auth/me` — after logout | **401** |

The authenticate → authenticated GET → logout → rejected chain is proven **twice**, independently.

**Two precision notes, so the record is not read as claiming more than it shows:**

1. **Post-logout rejection was observed on `/api/auth/me`, not `/api/roster`.** The SPA gates on
   `/api/auth/me` and redirects to login before issuing `/api/roster`, so no post-logout
   `/api/roster` request reaches the server. `/api/auth/me` is a protected route returning 401 after
   logout, twice — session termination is demonstrated, but on that endpoint.
2. **The fresh-OTP tier is proven positively, not negatively.** `GET /api/decisions.csv` returned
   200 at **+1m56s**, inside the 5-minute window — correct behaviour, and paired with the
   unauthenticated **401** on the same route at 15:15:00 it demonstrates the gate admits fresh
   sessions and refuses unauthenticated ones. It does **not** demonstrate that an *aged* session is
   refused, which is the specific behaviour `COCKPIT_AUTH_BYPASS` used to disable. That negative
   case remains unproven by observation and rests on the restored unconditional guard (§H.1), the
   absent bypass variables (§H.5), the startup-refusal test (§H.5), and the static route inventory
   (§D). Confirming it would take one non-mutating retry of the CSV export more than five minutes
   after a login.

### H.6 Blast-radius confirmation

`hermes-gateway` active/enabled with `ActiveEnterTimestamp = 2026-08-01 21:51:14 UTC` — **unchanged,
not restarted**. `shift-agent-backup.timer` active/enabled. WhatsApp env keys intact (8). Gateway,
bridge, Hermes plugins, WhatsApp, routing, Catering stores and queue state untouched. Only
`shift-agent-cockpit` and `nginx` were restarted.

### H.7 Rollback

```sh
TS=20260806-150453
cp -a /root/cockpit-containment-backups/config.py.$TS               /opt/shift-agent/cockpit/backend/app/config.py
cp -a /root/cockpit-containment-backups/nginx-shift-agent-cockpit.$TS /etc/nginx/sites-available/shift-agent-cockpit
cp -a /root/cockpit-containment-backups/hermes.env.$TS              /root/.hermes/.env
systemctl restart shift-agent-cockpit && nginx -t && systemctl restart nginx
```

Not exercised — no rollback was needed.

### H.8 Remaining risks

1. **Deployed-vs-committed drift is closed for `config.py` only.** Other Cockpit files were not
   diffed against `d01c88a`. A full deployed-vs-release audit remains open.
2. **The next Cockpit deploy must not reintroduce the hatch.** The fix lives on the box; the
   uncommitted worktree that authored it still exists. If anyone builds from that worktree, the
   hole returns. Disposition of that worktree is an open owner decision.
3. **Admin access now requires an SSH tunnel.** Anyone expecting `http://46.62.206.192:8080/` will
   find it unreachable — this is intended, but it is a workflow change.
4. **`ufw` remains inactive.** Not used as primary containment by ruling; the loopback bind is the
   control. Defence-in-depth firewalling is still unaddressed.
5. **Finding A** (`GATEWAY_ALLOW_ALL_USERS`, inert) untouched and still fleet-owned.
6. **§G deferred** — the authorization-tier review for the 18 non-step-up mutating routes was
   explicitly out of scope and remains open.

### H.9 HIGH recurrence risk — the foreign worktree that authored the escape hatch

```
.config/superpowers/worktrees/SME-Agents/cockpit-temp-auth-bypass
```

Recorded here as a standing **HIGH recurrence risk**. It was **not modified, committed, reset or
cleaned** by the containment session, and must not be.

- **It authored the production escape hatch.** Its uncommitted diff to `web/backend/app/config.py`
  is exactly the `_env_flag` helper, the `auth_bypass_enabled` rewrite, and the
  `and not _env_flag("COCKPIT_AUTH_BYPASS_ALLOW_PROD")` guard weakening — byte-for-byte what was
  found deployed. Its `test_auth.py` diff adds
  `test_auth_bypass_allowed_on_prod_layout_with_explicit_temporary_override`, a test asserting the
  bypass is *permitted* on a production filesystem.
- **It remains dirty and uncommitted.** Three modified files, last touched 2026-06-03, not locked,
  HEAD on an unrelated June commit. Nothing in it exists on `origin/main`.
- **It is not an approved deployment source.** Production ran code from it that exists in no commit.
  The deployed file's mtime (Jun 3 00:38) matches the worktree edit (Jun 3 00:36).
- **Deploying from it would reintroduce the vulnerability.** The containment fixed the file on the
  box; it did not and cannot prevent a future build from this tree re-shipping the hatch.
- **Its owner must preserve any needed evidence, then retire or reconcile it.** Deleting it
  destroys the provenance record of how production came to run uncommitted code, so evidence
  capture precedes disposal. That decision belongs to the worktree's owner, not to this session.

The structural gap it exposes — nothing prevents deployment from a dirty or unversioned source — is
tracked as an open item, not closed by this containment.

### H.10 Confirmed incident wording

> Confirmed public information exposure and production step-up-authentication bypass; no confirmed
> primary-authentication bypass or unauthorized data access.

**Status as of 2026-08-06: CONTAINED.** The public exposure is closed at the network boundary, the
step-up bypass is removed from the running process, and the production guard is restored to
fail-closed so no environment variable can re-enable it.

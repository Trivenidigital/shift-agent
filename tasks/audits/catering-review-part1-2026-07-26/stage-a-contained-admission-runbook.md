# Stage A — contained-admission runbook — PREPARATION ONLY

**Status:** operator-ready runbook / **preparation only**. Producing or reviewing this document
authorizes nothing. Stage A remains **HOLD**. Execution requires the operator to (a) confirm ownership of
both allowlist candidates, (b) supply a separate operator-controlled denial-probe identity, (c) obtain a
live alias-separation proof, and (d) issue a separate proceed authorization.

**Authoritative starting state:** repo/deploy `dc7a81a2b6366f9c09fad86e7e07ee84a74c768d`
(`deploy-20260729-021058-dc7a81a2`); TE harness/control-socket/transport-budget **OFF**;
containment/allowlists **unchanged**; post-hoc Linux TE closure **GO**; Stage B / live-harness **NO-GO**.

**Drift-check tag:** `extends-Hermes` — operates the deployed **primary gateway admission gate**
(`_is_user_authorized`, driven by `GATEWAY_ALLOW_ALL_USERS` + `WHATSAPP_ALLOWED_USERS`, with built-in
phone↔LID alias expansion) plus the existing `identify-sender` alias resolver (a §2 precheck heuristic);
changes only two already-supported containment settings; adds no new mechanism. (The adapter's secondary DM
policy `_is_dm_allowed`/`allow_from` is a *separate* layer — see "Two authorization layers" below — that Stage
A does NOT rely on for containment.)

> **Reviewer amendment 1 (2026-07-31) — the two authorization layers were conflated; corrected below.** The
> earlier text presented the adapter's `_is_dm_allowed` → `sender_id in self._allow_from` as "the admission
> gate." That is the SECONDARY adapter DM policy, and on this release it is **OPEN by default** (see next
> section). The env keys Stage A actually mutates (`GATEWAY_ALLOW_ALL_USERS`, `WHATSAPP_ALLOWED_USERS`) drive
> the SEPARATE PRIMARY gateway gate `_is_user_authorized`, which is the effective containment boundary and
> which DOES resolve phone↔LID aliases at admission. §5/§6/§9 are corrected accordingly, and the phone/LID
> pair is now a hard same-person proof gate (see "Two authorization layers" + §1a).

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Inbound sender admission (containment) | Hermes IS the substrate — the PRIMARY gateway gate `_is_user_authorized` (`WHATSAPP_ALLOWED_USERS` + `GATEWAY_ALLOW_ALL_USERS`, phone↔LID alias-aware) is deployed; the adapter `_is_dm_allowed` DM policy is a separate default-open layer | Operate the existing primary allowlist gate; no new gate. |
| Phone/LID identity resolution (alias-expansion) | deployed operator tool `/usr/local/bin/identify-sender` (strict phone/LID dispatch) | Reuse `identify-sender` for the alias-separation precheck; no new resolver. |

Verdict: **extends-Hermes, config-and-verification only — nothing net-new to build.**

## Two authorization layers (do NOT conflate)

There are two independent WhatsApp admission layers on release `dc7a81a2`. Stage A's containment rests on
the **primary** one; the **secondary** one is open by default and Stage A does not change it. (Anchors read
read-only on-box; **re-verify at execution time** — Hermes core is out-of-tree.)

| Layer | Function | Fed by | Alias handling | Default | Stage A |
|---|---|---|---|---|---|
| **PRIMARY — gateway admission** (the effective containment boundary) | `_is_user_authorized(source)` — Hermes `run.py:6334` | `GATEWAY_ALLOW_ALL_USERS` (global allow-all, `run.py:6486`) + `WHATSAPP_ALLOWED_USERS` (per-platform allowlist, `run.py:6387,6599`) + DM-pairing list; **default: deny** | **YES** — phone↔LID resolved at admission via `_expand_whatsapp_auth_aliases` / `_normalize_whatsapp_identifier` (imported `run.py:~843-844`, used inside `_is_user_authorized`) | deny (once `GATEWAY_ALLOW_ALL_USERS` is false and the id is not allowlisted) | **THIS is what §4 mutates and what rejects the §6 denial probe.** |
| **SECONDARY — adapter DM policy** | `_is_dm_allowed(sender_id)` — Hermes `whatsapp.py:530-536`, called at `whatsapp.py:654` | `WHATSAPP_DM_POLICY` (or `config.extra.dm_policy`, `whatsapp.py:448`), default **`"open"`** + `allow_from`/`allowFrom` (NOT `WHATSAPP_ALLOWED_USERS`; only consulted when `dm_policy=="allowlist"`, `whatsapp.py:534-535`) | none in this method (raw `sender_id in self._allow_from`) | **`"open"` ⇒ admits ALL DMs** | **OPEN and UNCHANGED** — Stage A does not set `WHATSAPP_DM_POLICY`/`allow_from`, so this layer rejects nothing. |

Consequences the runbook now enforces:
- The §4 mutation configures the **primary** gate. The **primary** gate rejects the denial probe (default-deny
  after `GATEWAY_ALLOW_ALL_USERS=false` with the probe absent from `WHATSAPP_ALLOWED_USERS`). **`_is_dm_allowed`
  does not reject the probe** — it is `"open"` — so §5/§6 attribute admission/rejection to `_is_user_authorized`,
  and §6 records **which layer** logged the decision.
- **Alias expansion DOES occur at the effective admission gate** (the primary gate resolves phone↔LID). The
  earlier framing that admission is a raw-string membership test with "no alias expansion" applied only to the
  secondary `_is_dm_allowed` and is corrected. §2 (`identify-sender`) remains a proxy heuristic; §6 confirms the
  **live** alias-expansion behavior of the primary gate.
- **Optional defense-in-depth (record the decision, do not silently assume):** the operator MAY additionally
  set `WHATSAPP_DM_POLICY=allowlist` + `allow_from=<the two admitted forms>` so the secondary layer also
  contains. If they do NOT, the runbook must record that the adapter DM layer is intentionally left `"open"`
  behind the correctly-enforced primary rejection (which §3 snapshots and §6 proves). Leaving it open is
  acceptable ONLY because the primary gate is proven to reject (§6); it is not itself a containment guarantee.

**Scope boundary (Stage A stops here):** Stage A ends after containment (positive + negative admission)
evidence. It MUST NOT enable the TE control socket, the transport budget, the finalized-send harness, the
progressive-edit probe, or begin Stage B. It changes **only** the two authorized containment settings.

**Verified admission + alias facts this runbook rests on (read-only, release `dc7a81a2`):**
- **Effective admission = the PRIMARY gateway gate** `_is_user_authorized(source)` (Hermes `run.py:6334`,
  **out-of-tree Hermes core — verified read-only on-box, re-verify at execution time**): per-platform allowlist
  `WHATSAPP_ALLOWED_USERS` (`run.py:6387,6599`), DM-pairing list, global allow-all `GATEWAY_ALLOW_ALL_USERS`
  (`run.py:6486`), else **default deny**. For WhatsApp it **resolves phone↔LID aliases at admission** via
  `_expand_whatsapp_auth_aliases`/`_normalize_whatsapp_identifier` (imported `run.py:~843-844`, applied inside
  `_is_user_authorized`) — so an inbound `17329837841@s.whatsapp.net` (or the matching LID under WhatsApp
  privacy delivery) IS resolved to the allowlisted `+17329837841` form by the gate itself; alias handling is
  NOT purely an upstream `bridge.js` concern. The adapter's SECONDARY `_is_dm_allowed`/`allow_from`
  (`whatsapp.py:530-536,448,502-509`) is a *separate*, **default-`"open"`** layer (see "Two authorization
  layers") that Stage A leaves unchanged and that does NOT perform this admission. **`identify-sender` is a §2
  proxy heuristic, not the runtime admission normalizer**; **§6 (the live negative-admission probe) is the
  load-bearing containment check** and must record which layer logged the rejection, and §2 exists to catch
  obvious collisions cheaply, not to certify separation.
- Alias resolver used by §2 (proxy): `/usr/local/bin/identify-sender` — `(kind, normalized)`,
  `kind ∈ {phone, lid, invalid}`, strict `_LID_RE = ^\d{6,20}@lid$`, "never coincidentally treat a LID as a
  phone"; emits `{role, phone_normalized, …}`. It understands only `@s.whatsapp.net` / `@lid` and calls
  anything else (e.g. `@c.us`) `invalid` — a further reason it is a heuristic, not the runtime authority.
- **Phone/LID twin (the core alias risk):** WhatsApp can deliver the SAME account as a phone-JID **or** as a
  LID depending on privacy/contact state. The allowlist lists ONE form per party (`+17329837841`,
  `201975216009469@lid`); the operator MUST determine each party's actual delivery mode and, if a party can
  arrive as both a phone and a LID, list **both twin forms** — otherwise the pilot silently drops when it
  messages from the unlisted surface (looks like a dead agent, not broken containment).
- **Known-collision caution (verified anchor):** `201975216009469@lid` is Bangaru's LID, **roster-mapped to
  employee `e004` Anjali Iyer** (`src/agents/catering/scripts/catering-pattern-report:20-22`). This is an
  **employee identity**. Treat BOTH candidate identifiers as **unconfirmed** until the operator confirms
  ownership: if this LID's human is the pilot, their phone-form messages will drop; if not, admitting this
  LID admits an employee into the pilot. `identify-sender` on the LID cross-emits the roster person's
  `phone_normalized` (only if that phone is rostered) — compare it against the probe in §2.

Windows operators: every `ssh` step uses the two-step pattern — `ssh -o BatchMode=yes main-vps '<cmd>' >
~/.out.txt 2>&1` then open `~/.out.txt`. All prechecks are **read-only**; only §4 mutates, and only two
settings.

---

## 1. Human-input block (operator must type both, verbatim)

Stage A does not proceed until the operator supplies **both** tokens. These are gates, not narration.

```
ALLOWLIST CONFIRMED: <operator confirms ownership; the LID is admitted ONLY if the §1a
                      hard same-person gate proves it (else phone-only per §1a-B)>
  admit_phone = +17329837841
  admit_lid   = 201975216009469@lid   # ADMITTED ONLY IF §1a-A proves it is the SAME
                                       # operator account as admit_phone (NOT employee e004);
                                       # otherwise REMOVE it and admit the phone alone (§1a-B).
  (operator asserts + §1a proves via the live session mapping: the admit set is ONE
   operator in its transport form(s), NOT two separate people; this is the complete admit set.)

DENIAL PROBE: <a SEPARATE, operator-controlled identity used only to prove rejection>
  probe_identity = <operator-supplied phone-JID or LID>
  (operator asserts: this identity is controlled by the operator, is NOT the pilot
   party, and its alias set intersects NEITHER admitted representation — proven in §2 + §6.)
```

**Fail closed** if either token is missing or the operator cannot assert distinct control. A raw
string-equality / substring check here is a weak first filter only — it will NOT catch the most likely
aliasing mistake (the phone-JID twin `17329837841@s.whatsapp.net` is not a substring of `+17329837841`, yet
normalizes to it). **The load-bearing alias checks are §2 (heuristic) and §6 (the live reject probe), not
this substring test.** No proceed without both tokens.

---

## 1a. HARD GATE — phone/LID must be ONE operator, proven by the live session mapping

Stage A containment must admit **one operator in the necessary transport representations — NOT two separate
people.** The proposal lists `+17329837841` (phone) and `201975216009469@lid` (LID), but the LID is
**roster-mapped to employee `e004` Anjali Iyer** (`src/agents/catering/scripts/catering-pattern-report:20-22`).
An operator statement that they "own both" is **insufficient** while the roster identifies another person.

This is a hard gate — proceed to §2 ONLY if EXACTLY ONE of the following holds:

**(A) Same-person proof — admit both forms.** Prove through the **live WhatsApp session mapping** (the
deployed bridge/session data, not a human assertion) that the phone and the LID are the SAME
operator-controlled WhatsApp account:

```bash
ssh -o BatchMode=yes main-vps '
  echo "== live LID<->phone session map (written by the bridge sender-id patch) =="
  cat /opt/shift-agent/state/lid-cache.json 2>/dev/null
  echo "== identify-sender cross-emit for the LID (does it resolve to +17329837841?) =="
  /usr/local/bin/identify-sender "201975216009469@lid"
  /usr/local/bin/identify-sender "+17329837841"
' > ~/.samepersoncheck.txt 2>&1
# open ~/.samepersoncheck.txt
```
PASS (A) requires the live map to link `201975216009469@lid` ↔ `17329837841@s.whatsapp.net`/`+17329837841`
(same account, both transport forms of ONE human), AND that this human is the intended operator — NOT
employee `e004`. If the live map instead links the LID to a DIFFERENT phone/person, (A) FAILS.

**(B) Remove the LID — admit the phone only.** If same-person cannot be proven from the live session mapping,
**remove `201975216009469@lid` from the allowlist proposal**; admit `WHATSAPP_ALLOWED_USERS=+17329837841`
alone (plus the phone's OWN LID twin if the live map shows the operator's phone can also arrive as a LID —
that LID, not `201975216009469@lid`). Do NOT admit an unproven LID that roster-maps to another person.

**Fail closed** if neither (A) nor (B) can be satisfied, or if the live map is ambiguous. The **denial probe
must be a THIRD, separately-controlled identity whose alias set intersects NEITHER admitted representation**
(neither the phone nor whichever LID is admitted under (A)/(B)) — proven live in §2 + §6.

---

## 2. Live alias-expansion precheck (read-only; run BEFORE any change)

Prove the denial probe resolves to **neither** allowlisted identity. Normalize all three via the deployed
resolver; compare normalized forms **and** resolved role/person.

```bash
ssh -o BatchMode=yes main-vps '
  for id in "+17329837841" "201975216009469@lid" "<probe_identity>"; do
    echo "=== $id ==="; /usr/local/bin/identify-sender "$id"
  done' > ~/.aliascheck.txt 2>&1
# then open ~/.aliascheck.txt
```

**Pass criteria (ALL required):**
1. `admit_phone` → `kind=phone`, `phone_normalized=+17329837841`.
2. `admit_lid` → `kind=lid` (an `@lid` is never coincidentally a phone). Record its resolved role/person.
3. `probe_identity` → resolves; its `phone_normalized` (if phone) or LID differs from BOTH admit forms,
   AND its resolved role/person differs from the admit party AND from the `admit_lid` roster person.
4. No admit/probe pair shares a `phone_normalized`, an `@lid`, or a resolved person.

**Fail closed on ANY of:** a resolver error/`invalid`; the probe normalizing to an admit form; the probe
resolving to the same person as either admit identifier (e.g. probe is the phone-JID whose LID is
`admit_lid`, or vice-versa); or ambiguity the resolver cannot settle. Ambiguity or collision ⇒ STOP; do
not proceed to §4.

> **§2 is a heuristic, not a gate.** It runs against `identify-sender` — a *proxy* normalizer that is NOT
> the runtime admission path (the runtime normalizer is `bridge.js`/gateway). §2 cheaply catches obvious
> collisions (a probe that resolves to an admit form or the same rostered person), but it cannot certify
> that the runtime would normalize the probe the same way. The real containment guarantee is **§6**: the
> live negative-admission probe fail-closes on *actual* admission regardless of §2's model. If §2 is
> ambiguous, STOP — do not lean on it as proof.

---

## 3. Pre-change evidence (read-only snapshot; capture ALL before §4)

Record every value below to the evidence record (§7). Any deviation from the authoritative starting state
is a NO-GO.

```bash
ssh -o BatchMode=yes main-vps '
  echo "== deploy + closure identity =="
  cat /opt/shift-agent/.commit-hash 2>/dev/null; echo
  ls -1t /opt/shift-agent/deploys/ 2>/dev/null | head -1
  echo "== harness/socket/budget OFF =="
  echo "GATEWAY_TRANSPORT_EVIDENCE_ENABLED=$(grep -c "^GATEWAY_TRANSPORT_EVIDENCE_ENABLED=1" /root/.hermes/.env)"
  echo "GATEWAY_TURN_SEND_BUDGET_ENABLED=$(grep -c "^GATEWAY_TURN_SEND_BUDGET_ENABLED=1" /root/.hermes/.env)"
  ls -la /run/shift-agent/transport-evidence.sock 2>&1 | tail -1   # expect: No such file
  echo "== gateway + bridge health =="
  systemctl is-active hermes-gateway
  curl -s http://127.0.0.1:3000/health
  echo "== process counts (expect exactly 1 gateway, 1 bridge child) =="
  pgrep -af "hermes-gateway|gateway/run.py" | grep -v grep | wc -l
  pgrep -af "bridge.js" | grep -v grep | wc -l
  echo "== current containment + allowlist values (PRIMARY gate: _is_user_authorized) =="
  grep -nE "^GATEWAY_ALLOW_ALL_USERS=|^WHATSAPP_ALLOWED_USERS=" /root/.hermes/.env
  echo "== SECONDARY adapter DM policy (default open; record whether it is open or allowlist) =="
  grep -nE "^WHATSAPP_DM_POLICY=|^WHATSAPP_ALLOW_FROM=" /root/.hermes/.env
  grep -nE "dm_policy|allow_from|allowFrom" /root/.hermes/config.yaml 2>/dev/null
  echo "== OTHER admit/scope surfaces (prove the 2 keys are the whole INBOUND surface) =="
  # The inbound DM allowlist is not the only scoping layer: a separate front-brain
  # outbound ENFORCE scope + per-feature FLYER_* allowlists also gate behaviour.
  # Snapshot them so §9 can assert each is <= the two authorized identities (or is
  # a separate outbound/feature scope Stage A is NOT changing).
  grep -nE "^FRONT_BRAIN_OUTBOUND_ENFORCE=|^FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST=|^FRONT_BRAIN_CONVERSE=" /root/.hermes/.env
  echo "FLYER_*_ALLOWLIST scopes present (Stage A changes NONE of these):"
  grep -cE "^FLYER_[A-Z_]*ALLOWLIST=" /root/.hermes/.env
  echo "== business-state fingerprint (RECURSIVE per-file sha — cannot miss a file or an in-place edit) =="
  # Real on-disk layout (verified against the catering/commerce/cf-router scripts): flat hyphenated
  # catering-*.json + the state/catering/ subdir (amendment_discriminator_budget.json) + state/commerce/
  # (carts/orders/payment_intents/payment_references) + state/flyer/. A recursive per-file hash catches
  # in-place edits that a count/glob would miss.
  find /opt/shift-agent/state/catering-* /opt/shift-agent/state/catering \
       /opt/shift-agent/state/commerce  /opt/shift-agent/state/flyer \
       -type f 2>/dev/null | sort | xargs -r sha256sum 2>/dev/null
  # decisions.log (the audit chokepoint, logs/ not state/) is append-only — sha AND line-count
  sha256sum /opt/shift-agent/logs/decisions.log 2>/dev/null; wc -l /opt/shift-agent/logs/decisions.log 2>/dev/null
  echo "== timers + config hashes =="
  systemctl list-timers --all 2>/dev/null | grep -c shift
  sha256sum /root/.hermes/.env /root/.hermes/config.yaml 2>/dev/null
' > ~/.prechange.txt 2>&1
# open ~/.prechange.txt
```

**Expected:** `.commit-hash` = `dc7a81a2…`; newest deploy = `deploy-20260729-021058-dc7a81a2`; both ENABLED
greps = 0; TE socket absent; gateway active; `/health` `status:connected`, `queueLength:0`; exactly 1
gateway + 1 bridge; `GATEWAY_ALLOW_ALL_USERS` and `WHATSAPP_ALLOWED_USERS` (PRIMARY gate) at their
**current** values (record them for rollback); the SECONDARY adapter DM policy recorded — `WHATSAPP_DM_POLICY`
expected absent/`open` (record it; §6 relies on the PRIMARY gate rejecting, so `open` here is acceptable and
intentional unless the operator adds the optional `allowlist`+`allow_from` defense-in-depth); **the
front-brain enforce-scope + `FLYER_*_ALLOWLIST` snapshot captured — and
the operator confirms every other allow-scope is either ⊆ the two authorized identities or a separate
outbound/feature scope Stage A leaves untouched** (do not proceed if a broader inbound scope is found that
Stage A's two keys would not contain); catering/flyer state fingerprints captured; timers as expected;
`.env` + config hashes captured. **Note the `.env` is a symlink to `/root/.hermes/.env` — always read/edit
the target, never `sed -i` the `/opt/shift-agent/.env` symlink (that destroys it and fail-closes the deploy
gate).**

---

## 4. Exact mutation procedure (the ONLY change Stage A makes)

Change **only** the two authorized containment settings (the PRIMARY-gate keys), on the `.env` **target**.
`WHATSAPP_ALLOWED_USERS` is the **§1a admit set** — both forms only if §1a-A proved same-person, else the phone
alone (§1a-B):

```
GATEWAY_ALLOW_ALL_USERS=false
# §1a-A (same-person proven):   WHATSAPP_ALLOWED_USERS=+17329837841,201975216009469@lid
# §1a-B (LID unproven→removed): WHATSAPP_ALLOWED_USERS=+17329837841
WHATSAPP_ALLOWED_USERS=<§1a admit set>
```

Procedure:
1. Back up the target: `cp -a /root/.hermes/.env /root/.hermes/.env.stageA-bak-<UTC>` (record its sha256).
2. Edit `/root/.hermes/.env` (the symlink target) to set exactly those two keys to exactly those values.
   Change **no** other line. Confirm with a diff of the two keys only.
3. Restart **only** the gateway: `systemctl restart hermes-gateway`. Restart nothing else.
4. Re-verify immediately (read-only):

```bash
ssh -o BatchMode=yes main-vps '
  systemctl is-active hermes-gateway
  pgrep -af "hermes-gateway|gateway/run.py" | grep -v grep | wc -l   # expect 1 (same pattern as §3)
  pgrep -af "bridge.js"                     | grep -v grep | wc -l   # expect 1
  curl -s http://127.0.0.1:3000/health                                # expect connected, queue 0
  grep -nE "^GATEWAY_ALLOW_ALL_USERS=|^WHATSAPP_ALLOWED_USERS=" /root/.hermes/.env
  # prove NOTHING ELSE changed: EXACTLY the two containment keys differ backup-vs-target.
  echo "== .env diff (backup vs new target) — expect ONLY the 2 containment keys =="
  diff /root/.hermes/.env.stageA-bak-<UTC> /root/.hermes/.env
  echo "== changed-line count (expect exactly 4: 2 old < + 2 new >) =="
  diff /root/.hermes/.env.stageA-bak-<UTC> /root/.hermes/.env | grep -cE "^[<>]"
  echo "TE flags still OFF:"; grep -cE "^GATEWAY_(TRANSPORT_EVIDENCE|TURN_SEND_BUDGET)_ENABLED=1" /root/.hermes/.env
  ls -la /run/shift-agent/transport-evidence.sock 2>&1 | tail -1   # still absent
' > ~/.postmutate.txt 2>&1
```

**Prove (all required):** exactly one gateway + one bridge; WhatsApp `connected`, queue 0; the two keys now
hold the target values; the `.env` differs from the pre-change file in **exactly** those two lines (diff
the backup vs the new target); TE + budget flags still `0` (no socket); **no** tool-surface / front-brain /
disabled-toolset / model / any other config line changed. Any extra diff line ⇒ STOP + rollback (§8).

---

## 5. Positive admission probe (allowlisted identity is admitted)

Operator sends one benign inbound message from an **allowlisted** identity (`admit_phone` or `admit_lid`).
Observe read-only:

```bash
ssh -o BatchMode=yes main-vps '
  journalctl -u hermes-gateway --since "-3 min" --no-pager | \
    grep -iE "_is_user_authorized|authoriz|allow|admit|inbound|process" | tail -20
' > ~/.pos.txt 2>&1
```

**Pass:** the message is admitted by the **primary gate** `_is_user_authorized` (reaches inbound processing /
an intended benign reply path).
**Guard:** catering **lead creation is content-triggered** (not approval-code-gated) — a substantive
catering-sounding inbound from the admitted pilot *can* create a lead + a `decisions.log` row. So EITHER
(a) send a message that provably cannot be parsed as a catering inquiry (e.g. a bare "hi"/"test") and assert
the §3 fingerprints are byte-identical, OR (b) explicitly accept that a lead may be created, and record +
fingerprint the exact resulting `catering-leads.json`/`decisions.log` delta as expected. Do NOT leave it
ambiguous, and never drive a `#XXXXX` approval or money flow. The positive probe proves *admission*, not
*business quiescence* — keep the two separate.

---

## 6. Negative admission probe (the separate identity is rejected pre-business)

Operator sends one benign inbound message from the **`probe_identity`** (the §1 denial probe). Observe
read-only:

```bash
ssh -o BatchMode=yes main-vps '
  journalctl -u hermes-gateway --since "-3 min" --no-pager | \
    grep -iE "_is_user_authorized|not authoriz|unauthor|not allowed|reject|deny|disallow|_is_dm_allowed|ignored" | tail -20
  curl -s http://127.0.0.1:3000/health   # queue still 0 — no outbound queued
' > ~/.neg.txt 2>&1
```

**Pass (ALL required):**
- The probe is **rejected at the PRIMARY gateway gate `_is_user_authorized`** (default-deny: `GATEWAY_ALLOW_ALL_USERS`
  false and the probe absent from `WHATSAPP_ALLOWED_USERS`, after the gate's phone↔LID alias expansion); no
  agent dispatch. **Record which layer logged the rejection** in the evidence record — it must be the primary
  gate, NOT the secondary `_is_dm_allowed` (which is `"open"` and would admit). If the journal shows admission
  reaching `_is_dm_allowed`/agent dispatch, the primary gate did NOT reject ⇒ fail closed + rollback.
- **No customer response** is generated; **no provider send** occurs (bridge queue stays 0; no outbound in
  journal for the probe chat).
- **No business mutation:** re-capture the §3 fingerprints — `catering-leads.json`,
  `catering-proposals.json`, `catering-quote-ledger.json`, `catering-menu.json`,
  `catering-menu-pending.json`, `catering-learning-summary.json`, the commerce stores
  (`orders.json`/`carts.json`/`payment_intents.json`/`payment_references.json`), `logs/decisions.log`
  (sha **and** line count — it is append-only), and flyer state are **byte-identical** to pre-change (no
  lead, proposal, quote, menu, order, owner notification, decision, or follow-up created or altered).

**Fail closed** if the probe is admitted, if any outbound is generated for it, or if any state fingerprint
changes. A fail here ⇒ STOP + rollback (§8) + report; do not retry blindly.

---

## 7. Before/after comparison + evidence-record template

Fill and archive alongside this runbook (do not commit into the frozen release; this is an ops evidence
record):

```
STAGE A CONTAINED-ADMISSION EVIDENCE — <UTC timestamp>
deploy: dc7a81a2 / deploy-20260729-021058-dc7a81a2   commit-hash: <captured>
TE socket: absent   TE flag: 0   budget flag: 0   (before AND after)
gateway procs: 1→1   bridge procs: 1→1   WhatsApp: connected→connected   queue: 0→0

same-person hard gate (§1a): <A: phone+LID proven SAME account via live session map, human = operator not e004
  | B: LID removed, phone-only admit set>   admit_set = <+17329837841[,<operator's own LID twin>] | +17329837841>

alias precheck (§2):
  admit_phone  -> kind=phone  normalized=+17329837841
  admit_lid    -> kind=lid    role/person=<...>   (only if admitted under §1a-A)
  probe        -> kind=<...>   normalized/role=<...>   distinct-from-EVERY admitted form + person: YES

layers snapshot (§3): PRIMARY _is_user_authorized fed by GATEWAY_ALLOW_ALL_USERS + WHATSAPP_ALLOWED_USERS;
  SECONDARY adapter WHATSAPP_DM_POLICY=<open|allowlist>  allow_from=<...>  (open = intentional; primary rejects)

containment change (§4):
  GATEWAY_ALLOW_ALL_USERS:  <old> -> false
  WHATSAPP_ALLOWED_USERS:   <old> -> <admit_set from §1a>
  .env diff lines vs backup: EXACTLY 2 (the two keys)   other config: UNCHANGED

positive probe (§5): admitted=YES via _is_user_authorized  business-mutation=NONE
negative probe (§6): rejected=YES at PRIMARY gate _is_user_authorized (which-layer logged=<primary>)
  customer-response=NONE  provider-send=NONE
  state fingerprints (catering-*.json + orders/carts/payment_* + decisions.log sha+lines + flyer): IDENTICAL before/after

verdict: <GO to record containment evidence / NO-GO + rollback>
```

---

## 8. Rollback procedure (restore exact prior containment)

Trigger on ANY unexpected effect (extra `.env` diff line, probe admitted, unexpected outbound, any state
mutation, health regression, more than one gateway/bridge):

1. **Stop immediately** — do not run further probes.
2. Restore the exact prior `.env`: `cp -a /root/.hermes/.env.stageA-bak-<UTC> /root/.hermes/.env`
   (verify sha256 == the §3 pre-change `.env` hash).
3. `systemctl restart hermes-gateway`.
4. Re-verify (read-only): `.commit-hash` = `dc7a81a2…`; `GATEWAY_ALLOW_ALL_USERS` /
   `WHATSAPP_ALLOWED_USERS` back to their pre-change values; exactly 1 gateway + 1 bridge; WhatsApp
   connected, queue 0; TE/budget flags still 0, socket absent; catering + flyer state fingerprints
   identical to §3.
5. Record the rollback in the evidence record. Do not re-attempt without a fresh operator authorization.

---

## 9. GO / NO-GO criteria

**GO to record containment evidence** iff ALL hold:
- §1 both human-input tokens supplied.
- **§1a hard same-person gate satisfied:** EITHER (A) the phone and `201975216009469@lid` are proven the SAME
  operator-controlled account via the **live session mapping** (and that human is the operator, not employee
  `e004`), OR (B) the LID is removed and the admit set is the phone (plus the operator's OWN LID twin if the
  live map shows one). An unproven LID that roster-maps to another person is a **NO-GO**.
- §2 alias precheck passed as a **heuristic**: the probe resolves to **neither admitted form and neither
  admitted person** (no ambiguity). §2 alone is NOT sufficient — §6 is the load-bearing check.
- §3 pre-change state == authoritative starting state (dc7a81a2, TE/budget OFF, 1+1 procs, connected); PRIMARY
  gate keys recorded; SECONDARY adapter DM policy (`WHATSAPP_DM_POLICY`/`allow_from`) recorded (default `open`
  acceptable because §6 proves the PRIMARY gate rejects); front-brain enforce-scope + `FLYER_*` allowlists
  snapshotted and confirmed to leave the primary-gate keys as the complete inbound admission surface.
- §4 exactly the two PRIMARY-gate containment keys changed; `.env` diff == 2 keys (4 changed lines); TE/budget
  still OFF; 1 gateway + 1 bridge; WhatsApp connected.
- §5 allowlisted identity admitted **by `_is_user_authorized`**; any business mutation explicitly recorded +
  fingerprinted (not left ambiguous).
- §6 probe rejected **at the PRIMARY gate `_is_user_authorized`** (which-layer logged the rejection recorded,
  and it is the primary gate — the secondary `_is_dm_allowed` is `open` and must NOT be relied on): no
  response, no provider send, zero business-state mutation (recursive fingerprints + `decisions.log` line
  count identical). **This is the load-bearing containment proof.**

**NO-GO / rollback** if ANY of: missing/ambiguous human input; **§1a unmet (LID unproven AND not removed)**;
probe not distinct from an admitted form OR admitted person; alias collision; pre-change drift; more than the
two keys changed; probe admitted; **rejection not attributable to the primary gate (e.g. it reached
`_is_dm_allowed`/agent dispatch)**; any outbound for the probe; any catering/flyer state fingerprint change;
health regression; more than one gateway or bridge.

**Stop after containment evidence.** Do NOT, under this runbook, enable the TE control socket, the transport
budget, the finalized-send harness, or the progressive-edit probe, and do NOT begin Stage B. Those require
separate, explicit operator authorization.

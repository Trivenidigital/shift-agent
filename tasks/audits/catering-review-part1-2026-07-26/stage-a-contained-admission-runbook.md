# Stage A — contained-admission runbook — PREPARATION ONLY

**Status:** operator-ready runbook / **preparation only**. Producing or reviewing this document
authorizes nothing. Stage A remains **HOLD**. Execution requires the operator to (a) confirm ownership of
both allowlist candidates, (b) supply a separate operator-controlled denial-probe identity, (c) obtain a
live alias-separation proof, and (d) issue a separate proceed authorization.

**Authoritative starting state:** repo/deploy `dc7a81a2b6366f9c09fad86e7e07ee84a74c768d`
(`deploy-20260729-021058-dc7a81a2`); TE harness/control-socket/transport-budget **OFF**;
containment/allowlists **unchanged**; post-hoc Linux TE closure **GO**; Stage B / live-harness **NO-GO**.

**Drift-check tag:** `extends-Hermes` — operates the deployed Hermes WhatsApp adapter's existing DM
allowlist policy (`_is_dm_allowed` → `sender_id in self._allow_from`) and the existing `identify-sender`
alias resolver; changes only two already-supported containment settings; adds no new mechanism.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Inbound sender admission / DM allowlist | Hermes IS the substrate — `WhatsAppAdapter._is_dm_allowed` allowlist policy is deployed | Operate the existing allowlist; no new gate. |
| Phone/LID identity resolution (alias-expansion) | deployed operator tool `/usr/local/bin/identify-sender` (strict phone/LID dispatch) | Reuse `identify-sender` for the alias-separation precheck; no new resolver. |

Verdict: **extends-Hermes, config-and-verification only — nothing net-new to build.**

**Scope boundary (Stage A stops here):** Stage A ends after containment (positive + negative admission)
evidence. It MUST NOT enable the TE control socket, the transport budget, the finalized-send harness, the
progressive-edit probe, or begin Stage B. It changes **only** the two authorized containment settings.

**Verified admission + alias facts this runbook rests on (read-only, release `dc7a81a2`):**
- Admission: `WhatsAppAdapter._is_dm_allowed(sender_id)` — allowlist policy returns
  `sender_id in self._allow_from` (Hermes `whatsapp.py:531-537`, **out-of-tree Hermes core — verified
  read-only on-box, re-verify at execution time**); `_allow_from` is the comma-split of the configured allow
  list (`_coerce_allow_list`, `whatsapp.py:449, 503-509`). The membership test itself is on the `sender_id`
  string. **IMPORTANT — `identify-sender` is NOT the runtime admission normalizer.** For `+17329837841`
  (E.164-with-plus) to match, an inbound WhatsApp JID (`17329837841@s.whatsapp.net`, or a LID under WhatsApp
  privacy delivery) must be normalized to the allowlisted form **upstream** — in `bridge.js` / the gateway
  — *before* the membership test. So §2's `identify-sender` check is a **heuristic against a proxy
  normalizer**, not a proof of the runtime path; **§6 (the live negative-admission probe) is the
  load-bearing containment check**, and §2 exists to catch obvious collisions cheaply, not to certify
  separation.
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
ALLOWLIST CONFIRMED: <operator confirms ownership of BOTH admitted identifiers>
  admit_phone = +17329837841
  admit_lid   = 201975216009469@lid
  (operator asserts: both belong to the intended pilot party; the LID is NOT a
   third party's roster identity; this pair is the complete admit set.)

DENIAL PROBE: <a SEPARATE, operator-controlled identity used only to prove rejection>
  probe_identity = <operator-supplied phone-JID or LID>
  (operator asserts: this identity is controlled by the operator, is NOT the
   pilot party, and the operator consents to it being admission-tested.)
```

**Fail closed** if either token is missing or the operator cannot assert distinct control. A raw
string-equality / substring check here is a weak first filter only — it will NOT catch the most likely
aliasing mistake (the phone-JID twin `17329837841@s.whatsapp.net` is not a substring of `+17329837841`, yet
normalizes to it). **The load-bearing alias checks are §2 (heuristic) and §6 (the live reject probe), not
this substring test.** No proceed without both tokens.

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
  echo "== current containment + allowlist values =="
  grep -nE "^GATEWAY_ALLOW_ALL_USERS=|^WHATSAPP_ALLOWED_USERS=" /root/.hermes/.env
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
gateway + 1 bridge; `GATEWAY_ALLOW_ALL_USERS` and `WHATSAPP_ALLOWED_USERS` at their **current** values
(record them for rollback); **the front-brain enforce-scope + `FLYER_*_ALLOWLIST` snapshot captured — and
the operator confirms every other allow-scope is either ⊆ the two authorized identities or a separate
outbound/feature scope Stage A leaves untouched** (do not proceed if a broader inbound scope is found that
Stage A's two keys would not contain); catering/flyer state fingerprints captured; timers as expected;
`.env` + config hashes captured. **Note the `.env` is a symlink to `/root/.hermes/.env` — always read/edit
the target, never `sed -i` the `/opt/shift-agent/.env` symlink (that destroys it and fail-closes the deploy
gate).**

---

## 4. Exact mutation procedure (the ONLY change Stage A makes)

Change **only** the two authorized containment settings, on the `.env` **target**:

```
GATEWAY_ALLOW_ALL_USERS=false
WHATSAPP_ALLOWED_USERS=+17329837841,201975216009469@lid
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
    grep -iE "allow|admit|_is_dm_allowed|inbound|process" | tail -20
' > ~/.pos.txt 2>&1
```

**Pass:** the message is admitted (reaches inbound processing / an intended benign reply path).
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
    grep -iE "not allowed|reject|deny|disallow|_is_dm_allowed|ignored" | tail -20
  curl -s http://127.0.0.1:3000/health   # queue still 0 — no outbound queued
' > ~/.neg.txt 2>&1
```

**Pass (ALL required):**
- The probe is **rejected at gateway admission** (`_is_dm_allowed` → False; no agent dispatch).
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

alias precheck (§2):
  admit_phone  -> kind=phone  normalized=+17329837841
  admit_lid    -> kind=lid    role/person=<...>
  probe        -> kind=<...>   normalized/role=<...>   distinct-from-both: YES

containment change (§4):
  GATEWAY_ALLOW_ALL_USERS:  <old> -> false
  WHATSAPP_ALLOWED_USERS:   <old> -> +17329837841,201975216009469@lid
  .env diff lines vs backup: EXACTLY 2 (the two keys)   other config: UNCHANGED

positive probe (§5): admitted=YES  business-mutation=NONE
negative probe (§6): rejected=YES  customer-response=NONE  provider-send=NONE
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
- §1 both human-input tokens supplied; probe distinct from both admit identities; each party's phone/LID
  twin forms resolved (both twin surfaces listed if a party can arrive as both).
- §2 alias precheck passed as a **heuristic** (probe resolves to neither admit identity — form AND person;
  no ambiguity). §2 alone is NOT sufficient — §6 is the load-bearing check.
- §3 pre-change state == authoritative starting state (dc7a81a2, TE/budget OFF, 1+1 procs, connected); AND
  the front-brain enforce-scope + `FLYER_*` allowlists snapshotted and confirmed to leave the two
  containment keys as the complete inbound admission surface (no broader uncontained inbound scope).
- §4 exactly the two containment keys changed; `.env` diff == 2 keys (4 changed lines); TE/budget still OFF;
  1 gateway + 1 bridge; WhatsApp connected.
- §5 allowlisted identity admitted; any business mutation explicitly recorded + fingerprinted (not left
  ambiguous).
- §6 probe rejected at admission: no response, no provider send, zero business-state mutation (recursive
  fingerprints + `decisions.log` line count identical). **This is the load-bearing containment proof.**

**NO-GO / rollback** if ANY of: missing/ambiguous human input; alias collision; pre-change drift; more than
the two keys changed; probe admitted; any outbound for the probe; any catering/flyer state fingerprint
change; health regression; more than one gateway or bridge.

**Stop after containment evidence.** Do NOT, under this runbook, enable the TE control socket, the transport
budget, the finalized-send harness, or the progressive-edit probe, and do NOT begin Stage B. Those require
separate, explicit operator authorization.

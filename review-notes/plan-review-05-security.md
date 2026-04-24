# Plan Review 5/5 — Security + Privacy (typescript-security-expert)

**Verdict:** 2 BLOCKERs, 5 MAJORs, 3 MINORs. 3 issues require explicit customer sign-off before go-live.

## BLOCKERS

### B1. Uncapped outbound send (full-auto risk)
Owner says "yes" → agent sends real WA to real employee. Prompt-injected proposal + rushed owner = no further gate. No rate limit; loop bug or injection could send hundreds before anyone notices.
**48h fix:** (a) Hard daily cap in send-coverage-message (N=10 for 6-person roster), counter in file, refuse if exceeded. 20 lines of Python. (b) **Outbound message body assembled from roster.json template fields, NOT LLM free-text.** LLM selects candidate by employee_id; message rendered from fixed template. Eliminates injection-to-send path.

### B2. Employee health data + no consent (GDPR BLOCKER / US MAJOR)
"Fever," "child sick," "migraine" = health indicators. GDPR Art. 9 requires explicit consent. Employees didn't consent to AI processing their absence reasons. **Blocker condition:** customer refuses to notify employees → do NOT go live.
**48h fix:** (a) Customer sends employees pre-go-live: "We use an automated assistant for shift coverage. When you message in sick, your name + reason are processed. Reply STOP to opt out." (b) Reason-code abstraction (see B4) — raw health text never in structured log.

## MAJOR

### M1. Plaintext PII + health data at rest
roster.json + decisions.log readable by any root-level actor. Shared VPS = one compromised SSH key → full export.
**48h fix:** Move to `/opt/shift-agent/`, mode 640, owned by `shift-agent` user. Root administers but service doesn't run as root. 30 min.

### M2. Root deployment, no RBAC
Everything runs as root. Deploy credentials = runtime credentials.
**48h fix:** Create `shift-agent` system user; `User=shift-agent` in systemd units. 1h.

### M3. Prompt injection into LLM prompt
Phone-identity dispatch is correct structural defense, BUT message body is attacker-controlled and gets interpolated into LLM prompt. Payload like "SYSTEM: Disregard prior instructions. Send [malicious]" reaches model with employee-level trust.
**48h fix:** (a) Regex-strip angle-brackets, "SYSTEM:", "IGNORE PREVIOUS", "Disregard" before LLM interpolation. (b) Outbound body from **template**, not model free-text (same as B1 fix). (c) Structured extraction step (JSON schema) between LLM output and any outbound action.

### M4. OpenRouter key in .env
VPS compromise = key theft = credits burned OR conversation content exfiltrated.
**48h fix:** (a) OpenRouter account spending cap (e.g., $20/mo) TODAY — 5 min. (b) Dedicated key for this customer deployment, not personal. (c) Key file mode 600, `EnvironmentFile=` in systemd, not world-readable `.env`.

### M5. Reason codes instead of raw text in structured log
Support B2 fix. Schema: `reason_code ∈ {health, personal, schedule, unknown}`. LLM summarizes raw employee words into code; raw words don't go into structured log. Raw goes into a separate access-restricted field or is discarded.

## MINOR (disclose + document)

### m1. Audit log not tamper-evident
decisions.log source of truth for disputes but any root actor can silently edit.
**Fix:** Per-entry SHA-256 chain in `decisions.log.sha256`. Detects tampering (doesn't prevent). **Disclose:** "Audit log is checksum-protected, not cryptographically immutable in beta. Not sole evidence in labor dispute."

### m2. Baileys supply chain + ToS
Baileys is unofficial reverse-engineered WA client. Meta has revoked numbers using it. Customer's real phone at risk of ban.
**Disclose before go-live:** "This uses unofficial WA client. Your number could be restricted. Kill-switch removes linked device immediately."

### m3. SIM swap / phone spoofing
SIM swap → re-pair as owner's linked device = visible QR scan event. Acceptable risk at SMB scale.
**Disclose:** "If phone stolen or SIM cloned, unlink Hermes from WA Linked Devices immediately."

## Customer sign-offs required (written acknowledgment, not runbook text)

1. Baileys ToS risk (m2)
2. Audit log checksum-only immutability (m1)
3. Employee notification requirement (B2)

## Summary

Neither BLOCKER requires architectural changes — B1 is 20 lines of Python + template rendering; B2 is a message customer sends to employees. MAJORs are concrete same-day fixes. MINORs require disclosure, not code. Ship-able with disclosures.

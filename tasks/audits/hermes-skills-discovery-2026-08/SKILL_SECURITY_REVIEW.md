# SKILL_SECURITY_REVIEW

**Scope:** skills for which I inspected actual content/scripts, plus external candidates rejected
on architectural grounds. **No skill was installed, modified, enabled, or disabled.**
**Date:** 2026-08-02.

**Standing principle (inherited, endorsed):** official/bundled/builtin provenance is **not**
proof of safety. Successful installation and a passing `audit` are **not** proof of runtime
correctness.

---

## 1. `godmode` — measured assessment

**OBSERVED FACT**
- Path `<HERMES_HOME>/skills/red-teaming/godmode`; source `local`, trust `local`,
  status `enabled` on **all three hosts**.
- Description (the routing trigger): `"Jailbreak LLMs: Parseltongue, GODMODE, ULTRAPLINIAN."`
- Ships **4 scripts**, 152K.
- **0 log hits** across gateway/platform logs — no evidence it has ever been selected.

**INTERPRETATION** — A jailbreak/prompt-attack skill is enabled fleet-wide, including on the
customer-facing WhatsApp host. Its description is semantically distant from any business
workflow, so routine mis-selection is unlikely; but it is selectable in principle, and the fleet
has no disable state at all (Finding S-1).

**RISK** — Two channels: (a) an adversarial inbound message crafted to trigger jailbreak-skill
selection on a customer-facing host; (b) routing-surface noise. Neither is demonstrated.

**RECOMMENDED ACTION** — Disable on main-vps and vpin-vps. Its value to this fleet is
approximately zero, so the cheapest correct action is removal from the routing surface rather
than further investigation. Retain on a non-production host only if the Gecko/security workstream
wants it for red-team exercises.

**BLOCKING STATUS** — **NOT blocking.** Wave-0 hygiene item.

**RULING: `QUARANTINE_CANDIDATE`.** Explicitly **not** `ACTIVE_SECURITY_DEFECT` — there is no
proof of meaningful runtime reachability having occurred and no demonstrated dangerous capability
exercised. The four scripts were not read line-by-line; that is the remaining evidence gap and it
is not needed to justify disabling a zero-value skill.

## 2. `rest-graphql-debug` — Gecko precedent vs live state

**OBSERVED FACT** — Installed and `enabled` on srilu-vps. Source `official`. **0 scripts**, 20K —
prose/instruction only. 0 log hits. The Gecko ruling of record is
`QUARANTINED_REFERENCE_ONLY` following a DANGEROUS scan verdict that was auto-allowed on
official provenance.

**INTERPRETATION** — The ruling is **not enforced at the skill layer**: the skill sits `enabled`
like everything else. Materially, though, a 0-script skill cannot execute anything itself; it can
only instruct the model. That is a meaningfully lower risk profile than a script-bearing skill and
is consistent with "reference only" in substance if not in configuration.

**RISK** — Low in isolation; the real risk is **precedent drift** — a documented quarantine that
no mechanism enforces will not hold for the next skill either.

**RECOMMENDED ACTION** — Gecko workstream to set the state to match its own ruling. Fleet-wide,
adopt an enforceable quarantine mechanism (see `CUSTOM_SKILLS_BACKLOG.md`
→ `org/skill-promotion-and-rollback`).

**BLOCKING STATUS** — **NOT blocking** for Shift/Catering/Flyer adoption. Gecko-owned.

**RULING: `CURRENT_NONBLOCKING_DEFECT`** (configuration does not match the ruling of record).

## 3. `solana` — guarded path exists; invocation unproven

**OBSERVED FACT** — Installed, `enabled`, srilu-vps, `official`, 1 script, 44K. The guarded
wrapper `/usr/local/bin/gecko-solana-verify` **is present** (mtime 2026-08-01 22:50).

**INTERPRETATION** — A naive log grep returned 429 hits for the string "solana", **which I am
explicitly not treating as evidence of skill invocation**: srilu runs a Solana-centric trading
system, so the token appears in logs for obvious unrelated reasons. Word frequency is not
invocation telemetry.

**RISK** — The documented concern (silent fallback to public `api.mainnet-beta.solana.com`
returning exit 0 when `SOLANA_RPC_URL` is absent) is a property of **direct unguarded use**. The
wrapper exists; whether the skill routes through it was **not** established.

**RECOMMENDED ACTION** — Gecko workstream to confirm the skill cannot be selected in a path that
bypasses `gecko-solana-verify`, and to verify `SOLANA_RPC_URL` presence in the consuming process
environment (not the `.env` file — that distinction has already produced two wrong conclusions in
this engagement).

**BLOCKING STATUS** — **NOT blocking** for this engagement. Gecko-owned.

**RULING: `INCONCLUSIVE`** on enforcement; the guarded path is confirmed present.

## 4. `evm`

**OBSERVED FACT** — Installed, `enabled`, srilu-vps, `official`, 1 script, 76K, 0 log hits.

**INTERPRETATION / RISK** — Documented concerns (CoinGecko enrichment hangs without adequate
timeout; queried addresses/contracts disclosed to CoinGecko; decodes only mined tx hashes;
proxy-detection false negatives) make it unsuitable for authorization or pre-sign validation —
a ruling this review endorses and does not broaden.

**RECOMMENDED ACTION** — Gecko-owned. Confirm no authorization/pre-sign path can reach it.

**BLOCKING STATUS** — **NOT blocking.** **RULING: `CURRENT_NONBLOCKING_DEFECT`** (enabled without
an enforcing constraint, consistent with S-1).

## 5. External candidates REJECTED on architectural grounds

From `hermes skills search` against the Hermes-supported registries (Skills Hub aggregating
clawhub, skills.sh, browse-sh):

| Candidate | Source/Trust | Verdict | Reason |
|---|---|---|---|
| `whatsapp-messaging` — "Send WhatsApp messages, manage templates, handle media" | clawhub / community | **REJECT** | Directly transfers **outbound-send policy** to an LLM-selected skill. Our send path is the deterministic `safe_io` chokepoint + `ScreenedWhatsAppAdapter`. Non-negotiable boundary violation |
| `oo-whatsapp` — "Use for ANY WhatsApp req..." | clawhub / community | **REJECT** | Same boundary violation, plus an extremely broad trigger phrase that would contest routing with `dispatch_shift_agent` on every inbound |
| `skills-sh/membranedev/.../whatsapp` | skills.sh / community | **REJECT** | Same; provenance thin ("Indexed by skills.sh from …") |
| `customer-support`, `customer-support-autopilot`, `afrexai-customer-support`, `afrexai-support-operations` | clawhub / community | **DEFER — `REFERENCE_ONLY`** | Plausible shape for Catering intake, but community trust, unknown network/telemetry behaviour, and they encode their own ticketing model. Our intake must bind to approved menu/pricing data — `/learn` from our proven flow is safer (see backlog) |
| `code-review`, `ai-code-audit`, `smart-code-review` | clawhub / community | **REJECT — duplicate + provenance** | Three near-identical Chinese-language forks of the same tool; we already have `claude-code`, `codex`, and an established PR-review practice |
| `browse-sh/*` (amazon, bestbuy, booking, depop …) | browse-sh / community | **NOT_APPLICABLE** | Site scrapers surfaced by keyword collision (e.g. query "brand"); no mapping to any observed workflow |

**Registry-quality note:** searches for our actual domains returned mostly community-trust,
low-signal results, several being forks of one another. This directly supports the brief's
"registry size ≠ quality" caution and reinforces the §3 conclusion in the skills inventory: our
best near-term value is already installed.

## 6. Method for the not-yet-inspected majority

No skill outside §§1–5 has been source-inspected. Before any shortlisting, each candidate must be
run through: shell/script contents · package installation · credential access · network
destinations · telemetry · third-party APIs · fallback behaviour · timeouts · exit codes ·
retries · destructive commands · filesystem and repo scope · cross-agent data exposure ·
memory/session access · hidden state mutation · subprocess behaviour · prompt-injection exposure ·
provenance · dependency pinning · update mechanism.

**No recommendation in `AGENT_SKILL_MATRIX.csv` is marked beyond `INSTALL_CANDIDATE` without
that inspection**; everything not inspected is capped at candidate status with inspection as a
Wave-0 gate.

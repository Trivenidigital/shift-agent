# Wave-2 W-1 — Multi-Location nearest-store READ via the plugin-tool path

**Drift-check tag:** `extends-Hermes` — every substrate capability is used
unmodified; the addition is one thin tool adapter plus a project-specific
deterministic reply bound to the turn. One deliberate divergence from the
multi-location directive is disclosed in §2.

**Status:** PLAN — no code written. Awaiting review.
**Base:** `origin/main` @ `b160f84`.

---

## Hermes-first capability checklist

End-to-end flow, each step tagged. Net-new LOC counted only for `[net-new]`.

| # | Step | Tag + basis | LOC |
|---|---|---|---|
| 1 | Customer WhatsApp message arrives | `[Hermes]` — WhatsApp inbound source origin | 0 |
| 2 | Sender-context stamped; cf-router declines to intercept | `[Hermes]` — identity/role gating + deployed plugin hook | 0 |
| 3 | Gateway binds turn ContextVars (session/message id) | `[Hermes]` — pinned-runtime session binding, proven Wave-1 P1 | 0 |
| 4 | Recognise store-locator intent from ordinary wording | `[Hermes]` — LLM gateway text | 0 |
| 5 | Discover capability via `tool_search` → `tool_describe` | `[Hermes]` — skill dispatch / progressive Tool Search, proven W1 Cell C | 0 |
| 6 | Ask for city/ZIP when the customer gave none | `[Hermes]` — ordinary clarification turn, no tool call | 0 |
| 7 | Invoke `find_nearest_location` through the bridge | `[Hermes]` — tool invocation substrate | 0 |
| 8 | Validate bounded args (`address`, `top_n` 1..5) | `[net-new]` — project-specific bounds Hermes cannot know | ~15 |
| 9 | Invoke `closest-location.py` by exact argv | `[net-new]` — SKILL subprocess access exists but needs `skills`+`terminal`, both globally disabled | ~15 |
| 10 | Geocode, rank, haversine fallback | `[Hermes]` — bundled `productivity/maps`, already wrapped by the deployed kernel | 0 |
| 11 | Translate exit code + stdout into five distinct states | `[net-new]` — exit-2-vs-exit-3 semantics are project meaning | ~25 |
| 12 | Build deterministic reply; register turn override; fail closed | `[net-new]` — reuses #686 primitive; template + rule are project-specific | ~20 |
| 13 | Emit `multi_location_closest_lookup` audit row where truthful | `[Hermes]` — variant exists at `schemas.py:6947`; audit chokepoint deployed. Not written for states its fields cannot describe — see §8a | 0 |
| 14 | Return structured JSON to Hermes | `[Hermes]` — bridge tool-result round trip | 0 |
| 15 | Egress substitution via `ScreenedWhatsAppAdapter` | `[Hermes]` — deployed seam + #686 override | 0 |

**4 of 15 steps net-new (27%)** — under the red-flag threshold. Step 13 was
initially drafted `[net-new]` ("we need an audit row") — the documented common
miss — and corrected after reading the schema. **SKILL prose: zero.**

awesome-hermes-agent / optional-skills check: `productivity/maps` is already the
bundled locator substrate and is already wrapped. Nothing in `optional-skills/`
or the Nous MCP catalog adds a store-locator capability. Verdict: **zero new
substrate**; this is a reachability + egress-truthfulness change only.

---

## Drift-rule self-checks

- ✅ Read `src/agents/multi_location/scripts/closest-location.py` (`osrm_distance`
  at 129-147 returns `None` unconditionally; exit codes 0/1/2/3; `customer_input`
  omits raw address) before drafting the result contract.
- ✅ Read `src/platform/schemas.py` (`MultiLocationClosestLookup` at 6947,
  `LocationEntry` optional lat/lon) before deciding no schema change is needed.
- ✅ Read `src/platform/safe_io.py` (`register_turn_outbound_override`, seam
  precedence after kill switch + automation-control) before drafting §9.
- ✅ Read `src/plugins/shift-agent-read/compliance_tool.py` and `__init__.py`
  (inner-schema shape, `_bind_outbound`, package-relative import) as the pattern
  to mirror.
- ✅ Read `src/agents/multi_location/skills/customer_location_query/SKILL.md` and
  `multi_location_query/SKILL.md` only to identify obsolete behaviour (dispatcher
  regex, `skill_view`, `log-decision-direct` via `terminal`, `NOT_WIRED` shelving)
  that must NOT be revived.
- ✅ Read `docs/governance/projects/multi-location.md` (v1.0.0, blob `e432ebd8359d`)
  before drafting the deterministic boundary, and recorded the divergence in §2.

---

## 1. Goal

Make the existing customer-facing nearest-store capability reachable through the
Hermes 0.19.1 plugin/tool architecture, so that *"Which store is closest to
75001?"* is answered with correctly ranked, factually exact store details —
without enabling generic `skills` or `terminal`, and without reviving
dispatcher/SKILL routing.

Non-goals: OSRM work, maps-client redesign, owner cross-location queries, any
change to `closest-location.py`.

---

## 2. Repository evidence

Blob hashes at `b160f84`: `AGENTS.md` `00fc63bf5bb5` · engineering-directive
`31d7706d42b5` · project-registry `9608bda369eb` · shared-platform-directive
`9c6ce25607d5` · multi-location directive `e432ebd8359d` (v1.0.0).

**Kernel facts:** `osrm_distance()` returns `None` unconditionally (HOTFIX
2026-05-04, E2E-BUG-3: `maps_client.py` takes addresses not lat/lon, and
reverse-geocoding N locations breaks Nominatim's 1 req/s cap). So
**`source="haversine_fallback"` is the normal result today** and drive minutes
are an *estimate* (`haversine_km * 1.3 / 0.5`). Exit codes: 0 success · 1
invalid/unresolvable input · 2 `locations` empty · 3 no usable ranked locations.
`customer_input` already omits the raw address and rounds lat/lon to 2 decimals.
`MultiLocationClosestLookup` carries `source: Literal["osrm","haversine_fallback","not_configured"]`
— see §8a for which states it can truthfully describe.

**Obsolete, not to be revived:** `customer_location_query/SKILL.md` depends on
dispatcher regex routing, dispatcher-supplied `sender_role` gating, `skill_view`,
and `log-decision-direct` through **`terminal`** — all four unavailable.
`multi_location_query/SKILL.md` is `STATUS: NOT_WIRED`, shelved 2026-07-19 with
an unresolved cross-location privacy leak. Out of scope entirely.

**Disclosed divergence from the multi-location directive v1.0.0.** Its kernel
table names *"shared dispatcher store-locator regex"* as routing owner and
credits `customer_location_query/` with phrasing. This plan replaces **routing**
(dispatcher regex → Tool Search) and **phrasing** (SKILL prose → deterministic
template, §7). Every *deterministic* obligation — addresses, phones, hours,
ranking, `location_id`, surfacing `source` — is preserved or strengthened.
Recommend bumping the directive to v1.1.0 after this ships; **not** in this PR.

---

## 3. Affected projects (resolved with the real `GovernanceChecker`)

| Path | Project | Directive |
|---|---|---|
| `src/plugins/shift-agent-read/__init__.py` | shift-platform | shared-platform-directive.md |
| `src/plugins/shift-agent-read/plugin.yaml` | shift-platform | shared-platform-directive.md |
| `src/plugins/shift-agent-read/location_tool.py` (new) | shift-platform | shared-platform-directive.md |
| `tests/test_multi_location_read_tool.py` (new) | **multi-location** | projects/multi-location.md |
| `tasks/wave2-multi-location-read-plan.md` | repo-meta | projects/repo-meta.md |

Test filename chosen deliberately: `test_multi_location_read_tool.py` matches
`tests/test_multi_location*.py` → **multi-location**, the correct owner for a
multi-location behaviour test. `test_shift_agent_read_location_tool.py` would
match `tests/test_shift*.py` → shift-agent, the same filename artifact that cost
a CI round in #684.

`src/plugins/**` is a shift-platform `impact_analysis_path` → the PR body carries
shared-platform impact naming every agent on the shared gateway.

---

## 4. Capability Reuse Map (draft)

**Reused unchanged:** `closest-location.py`; `productivity/maps` via it;
`register_turn_outbound_override` + the egress seam; `MultiLocationClosestLookup`;
`_append_best_effort`; the live `shift_agent_read` toolset and its registration.

**Net-new:** one tool module (argv invocation, five-state translation,
deterministic reply, override registration).

**Not built:** geocoder, routing engine, OSRM client, parallel location store,
router/classifier, second plugin, second egress system, schema variant.

---

## 5. Exact changed-file proposal

| File | Change |
|---|---|
| `src/plugins/shift-agent-read/location_tool.py` | **new** — `find_nearest_location` |
| `src/plugins/shift-agent-read/__init__.py` | `from . import location_tool` + one `register_tool` |
| `src/plugins/shift-agent-read/plugin.yaml` | add tool to `provides_tools`; widen description to owner **and public** reads |
| `tests/test_multi_location_read_tool.py` | **new** |

**No change to `closest-location.py`** — its CLI already exposes everything
needed. If implementation finds a genuine blocker there, STOP and report.

---

## 6. Hermes vs deterministic ownership

**Hermes:** locator intent; deciding to call; asking for a place when missing
(prior turn, no tool call, no override); ordinary conversation.

**Deterministic:** configured roster; geocoding outcome handling; `location_id`,
`name`, `address_short`, `phone`, `hours`; ranking; `source` labelling; the exact
customer-facing store list.

**No identity gate** — public store information. The tool must not call
`identify-sender`, and must not read roster, schedule, pending, catering or
customer data. Result = public store facts + bounded lookup metadata only.

---

## 7. Successful-result contract

Args (identity-free): `address` (string, required), `top_n` (integer 1..5,
default 3). Handler → `closest-location.py --address <a> --top-n <n>`, exact
argv, never a shell string.

```json
{"ok": true, "status": "ranked", "source": "haversine_fallback",
 "estimate": true, "n_locations_total": 9, "n_returned": 3,
 "locations": [{"location_id","name","address_short","phone","hours",
                "drive_minutes","distance_km"}]}
```

`estimate` is derived (`source != "osrm"`), so it stays correct if OSRM is ever
wired. Registered reply:

> Estimated nearest locations to the address you gave:
> 1. {name} — {address_short} — {phone} — {hours} (~{drive_minutes} min)
> …
> Drive times are estimates.

Last line only when `estimate`. Fields verbatim from the script; missing optional
fields omitted, never invented.

---

## 8. Failure-result contracts

| Kernel | Tool result | Override | Customer text |
|---|---|---|---|
| exit 2 | `status:"not_configured"` | yes | "Store-location information isn't available right now. Please contact the store directly." |
| exit 1 | `status:"input_unresolved"` | yes | "I couldn't resolve that location. Please send a city, ZIP code, or street address." |
| exit 3 / empty / subprocess error | `status:"no_usable_locations"` | yes | same text as exit 2 (see below) |
| exit 0 + results | `status:"ranked"` | yes | store list (§7), closing "Anything else?" |
| bind fails | `{"ok":false,"refused":"outbound_truthfulness_guard_unavailable"}` — no locations, counts or status | n/a | Hermes handles; no facts supplied to corrupt |

`not_configured` vs `no_usable_locations` stay distinct: the first is an
operator never configuring a roster; the second is a roster where nothing is
rankable (e.g. entries lacking coordinates — the defect #678 fixed in the
template). Collapsing them hides an operator-actionable condition behind a
transient-sounding one. Subprocess timeout/OSError → `no_usable_locations`,
never a silent empty list. They nevertheless **share one customer sentence**,
because the difference is operator-actionable and not customer-actionable — the
structured result and the audit keep them apart for the operator.

---

## 8a. Audit coverage — the variant does NOT cover all five states

An earlier draft of this plan said `MultiLocationClosestLookup` "already fits"
every state. That is wrong, and the correction is the point of this section.

Its `source` field is `Literal["osrm", "haversine_fallback", "not_configured"]`.
**It covers the states for which its existing fields are truthful — no more.**

| State | Audit row | Why |
|---|---|---|
| `ranked` | yes, `source` verbatim from the kernel | every field truthful |
| `not_configured` (exit 2) | yes, `source="not_configured"` | that literal exactly describes what happened |
| `no_usable_locations` | **only if** the kernel reported one of the three literals | otherwise there is no truthful `source` to write |
| `input_unresolved` (exit 1) | **no row** | no literal describes "the customer's text could not be resolved"; `not_configured` would assert the operator has no roster, which may be false |
| bind failure | no row | the tool refuses and supplies no facts at all |

**Do not fabricate a `source` to satisfy the schema.** Writing
`source="not_configured"` for an unresolved input would put a false operational
fact in the durable log — an operator reading it would conclude the roster is
unset. A missing row is an honest gap; a wrong row is a durable lie.

**No `schemas.py` change in this PR.** Adding a fourth `source` literal or a new
variant to close the `input_unresolved` gap is a shared-platform change with its
own review surface, and the gap costs nothing operationally today (the customer
is asked to rephrase; no state moves). Recorded as a FOLLOW_UP, not done here.

---

## 9. Exact-turn override behaviour

Reuses #686 unchanged. Registration happens **inside the handler**, after the
effective session exists — `AIAgent.run_conversation` reassigns
`HERMES_SESSION_ID`, and a key read earlier fails *open*. Registration failure
fails closed at the tool. Precedence untouched: kill switch → automation-control
→ turn-bound override → optional front-brain tier.

**Why override on success here, when W1 compliance did not.** The risks are
opposite. For compliance the danger was over-claiming *absence*, so positive
rows were safe to leave to Hermes. Here the danger is corrupting *facts* — a
wrong address, phone or hours is HIGH under the directive because it sends real
people to the wrong place. So the factual success path is exactly what must be
deterministic. Cost in BQ-1.

---

## 10. Privacy

Never send raw address, full-precision lat/lon or query text to audit — the
existing variant already omits address by design. The tool adds nothing beyond
the kernel's `address_provided` style flag. If lat/lon reach audit, keep the
2-decimal (~1 km) convention. No customer identifier beyond what the variant
already carries.

---

## 11. Test matrix

**Contract:** inner-schema shape (no OpenAI wrapper); business-semantic
description; `address` required; `top_n` bounded 1..5; **no identity field**;
registered under `shift_agent_read`.

**Kernel translation** (subprocess stubbed by exit code + stdout): 0 → `ranked`
verbatim · 1 → `input_unresolved` · 2 → `not_configured` · 3 →
`no_usable_locations` · timeout/OSError → `no_usable_locations`.

**Truthfulness:** each state registers its exact template; `estimate` true iff
`source != "osrm"`; estimate sentence iff `estimate`; bind failure returns the
refusal with no locations/counts/status; addresses, phones, hours byte-equal to
kernel output.

**Privacy:** raw address absent from result and audit; lat/lon precision kept.

**Non-regression:** no `identify-sender` call; `test_agent_3_multi_location.py`,
`test_multi_location_config_template.py`,
`test_shift_agent_read_compliance_tool.py`, `test_safe_io_outbound_override.py`
unchanged and green.

No model sampling in CI. Linux for subprocess/`safe_io`; contract assertions
cross-platform.

---

## 12. Production LOC estimate

`location_tool.py` ~75 · `__init__.py` ~6 · `plugin.yaml` 0 → **~81 effective**,
inside the ≤100 target. STOP and report if implementation exceeds 150. Test LOC
~180 (excluded).

---

## 13. Deploy / activation sequence

**No activation needed** — `shift-agent-read` is already enabled and
`shift_agent_read` already in the WhatsApp toolset. Merge → rebuild with the
unmodified build gate (no `--skip-pytest`) → deploy normally → verify the tool
appears in the deferred catalog with its description, `skills`/`terminal` still
absent, W1 compliance tool still present, no config drift. **No config change,
no W1 re-activation.**

---

## 14. Rollback

Revert the PR and redeploy: the tool leaves the catalog; plugin and W1 keep
working. No state, no config, no migration. A faster disarm without deploying is
removing `shift_agent_read` from `platform_toolsets.whatsapp` — but that also
disarms W1 compliance, so it is a **shared** kill switch, not per-tool. Recorded
as a known coupling, not a defect to fix here.

---

## 15. Vertical E2E definition

1. Ordinary customer wording with a real address, no tool named → Hermes reaches
   `find_nearest_location` via `tool_search → tool_describe → tool_call`.
2. Adapter egress equals the deterministic template byte-for-byte; addresses,
   phones and hours match the configured roster exactly.
3. `source` surfaced, not suppressed; drive times presented as estimates while
   `haversine_fallback` is the source.
4. The directive's "OSRM-unavailable path still answers" requirement is satisfied
   by the same run, because that is the *only* path today — stated explicitly so
   nobody later reads a passing test as evidence OSRM was exercised.

Live E2E needs a configured roster. **Production has `locations: []`**, so only
`not_configured` is provable live without operator data. See BQ-3.

---

## 16. Explicitly rejected alternatives

1. **Revive `customer_location_query` + dispatcher regex** — needs `skills` and
   `terminal`, both disabled. The path Wave-1 proved unreachable.
2. **Second plugin for public reads** — no evidence `shift-agent-read` cannot
   host a public tool; per-tool authorization is a handler concern. Speculative.
3. **Wire OSRM / rewrite `maps_client.py`** — out of scope; the rate-limit reason
   still stands.
4. **Let Hermes phrase the store list** — rejected on the HIGH escalation for
   wrong contact details. See BQ-1.
5. **Shared "kernel subprocess" primitive** — one consumer today; wave rule needs
   2+.
6. **Owner cross-location queries** — shelved with an unresolved privacy leak.
7. **New audit variant / new `source` literal** — the existing variant covers the
   states its fields can describe truthfully (§8a); the one gap
   (`input_unresolved`) is left unaudited rather than closed with a
   shared-platform schema change in a read-only tool PR. FOLLOW_UP.

---

## 17. Blocking questions (3)

**BQ-1 — override on success drops compound questions.** Overriding the whole
reply guarantees exact contact details, but the customer's entire turn becomes
the store list. *"Which store is closest to 75001 and do you deliver?"* would get
only the list. Options: (a) accept — a dropped follow-up is recoverable, a wrong
address is not (**recommended**); (b) override only when the reply would contain
contact details — reintroduces wording inspection, so not deterministic; (c) add
a fixed "Anything else?" line to the template. Recommend **(a) + (c)**.

**BQ-2 — expose `top_n` to the model?** Exposing it honours "give me the two
closest"; not exposing keeps the schema to one field. Recommend **exposing**,
bounded 1..5 — it cannot affect factual correctness, only how many verbatim rows
are shown.

**BQ-3 — live E2E needs roster data we do not have.** Production has
`locations: []`; only `not_configured` is provable live. I will **not** invent
store addresses. Options: (a) merge + deploy, prove `not_configured` live, treat
`ranked` as test-covered until real roster data exists — status
`ACTIVE_NO_DATA`, mirroring W1 (**recommended**); (b) hold until roster data
exists; (c) operator supplies real locations during activation.

---

## 18. Status

- [x] Investigation complete; ownership resolved with the real checker
- [x] `/hermes-check` run; receipt written
- [x] Plan drafted
- [ ] Review + BQ answers
- [ ] Implementation (not started — no code written)

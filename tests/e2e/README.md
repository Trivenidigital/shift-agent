# Catering conversation E2E gate

LIVE-PARITY local twin of the production catering loop. It exercises the REAL
deterministic cf-router dispatch and the REAL catering scripts against a sandbox
state dir, and drives a FREE-FLOW LLM (OpenRouter, the tenant default
`openai/gpt-4o-mini`) as the Hermes brain under the ACTUAL `catering_dispatcher`
+ `creative_catering_proposals` SKILL.md prompts. It proves the *conversation*
(the acceptance/unit suites prove the invariants).

## What it covers (8 canonical turns + 1 probe)

1. Wedding inquiry over a stale lead → new lead + cross-ref + 2 grounded options.
2. Follow-up ("buffet") → R2A amendment capture.
3. **Mix-and-match** ("option 1 starters with option 2 mains") → deterministic
   `--recompose-from-sent` merge; delivered menu contains *exactly* the requested
   sections.
   - **3b (ambiguous probe)** "mix in option 3's desserts" (only 2 options exist)
     → the recompose tool sends ONE clarifying question, never a best-guess merge.
4. Price question → deferral line, no invented number, no menu re-dump.
5. Amendment ("135 guests") → R2A capture.
6. Off-menu ("lobster") → plain refusal + closest catalog alternatives + owner note.
7. Fresh contradicting event → lead #3 + cross-ref.
8. Silence → TTL sweep dormant (flag OFF), zero unprompted outbound.

## Run it (makes real model calls — env-gated so CI never does)

```bash
set -a; . scratch/.e2e-llm.env; set +a          # provides OPENROUTER_API_KEY
# full 3-session stability gate + artifacts:
python tests/e2e/catering_conversation_harness.py --out tests/e2e/artifacts
# or a single-session pytest (skips cleanly when the key is unset):
python -m pytest tests/e2e/test_catering_conversation_e2e.py -q -s
```

Artifacts (`e2e-transcript-prd.md`, `e2e-results-prd.json`) land in `--out`
(default `tests/e2e/artifacts/`, gitignored). The sandbox lives in a temp dir
(override with `E2E_SANDBOX_DIR`); it is never written into the repo tree.

Optional env: `E2E_LLM_MODEL` (default `openai/gpt-4o-mini` — the gate must hold
on the tenant model), `E2E_SANDBOX_DIR`.

## Fidelity notes / limitations

- The free-flow brain is a faithful *shape* of the gateway loop, not byte-for-byte
  the production tool-calling runtime: system = agent persona + both SKILLs verbatim
  + state + menu; the model replies naturally and may invoke a tool via a
  `<<<INVOKE …>>>` block that the harness runs against the sandbox.
- Catering scripts run **in-process** (SourceFileLoader + patched path constants)
  rather than as subprocesses — Windows/fcntl forces this; same code paths.
- `identify-sender` and owner/employee checks are stubbed to a deterministic
  customer identity (the `test_catering_pra_reachability` house seam); flyer off.
- On a box behind a TLS-intercepting proxy with a malformed CA the harness records
  a `tls_fallback_used` downgrade for the OpenRouter host only.

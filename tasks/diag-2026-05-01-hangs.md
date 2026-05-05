# Diagnosis: 2026-05-01 dispatcher "hangs"

**Drift-check tag:** `extends-Hermes` — the diagnosis itself proposes no code change, but the same workstream applied a substrate config change (`provider_routing: { sort: "price" }` to `/root/.hermes/config.yaml` on srilu-vps). The diag + that config change ship together, so the more honest tag is `extends-Hermes`. The downstream recommended fix (vision auxiliary credential injection) would also extend Hermes substrate behavior.

**Investigation date:** 2026-05-05
**Source:** `docs/hermes-alignment.md:115` documented incident (5+ min hangs, 320s, 11 api_calls, response=0 chars, observed twice on 2026-05-01 ~5h apart).

## TL;DR

The documented "dispatcher hangs" are **NOT a k2-thinking model failure mode**. They are vision auxiliary-client `401 AuthenticationError` loops triggering main-client tool-call thrashing. **Switching the dispatcher model would NOT fix this.** The root cause is in the Hermes auxiliary-client credential injection path. As of 2026-05-05, the underlying 401s are still actively occurring (64 occurrences in last 2000 log lines on srilu-vps).

## Evidence

### 1. Application-log signature (the primary finding)

`/opt/shift-agent/logs/hermes-gateway.log` contains repeated stack traces of this exact shape:

```
ERROR tools.vision_tools: Error analyzing image: Error code: 401 -
  {'error': {'message': 'Missing Authentication header', 'code': 401}}
Traceback (most recent call last):
  File "/usr/local/lib/hermes-agent/tools/vision_tools.py", line 581, in vision_analyze_tool
    response = await async_call_llm(**call_kwargs)
  File "/usr/local/lib/hermes-agent/agent/auxiliary_client.py", line 3708, in async_call_llm
    await client.chat.completions.create(**kwargs), task)
  ...
  File "/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/openai/_base_client.py", line 1698, in request
    raise self._make_status_error_from_response(err.response) from None
openai.AuthenticationError: Error code: 401 - {'error': {'message': 'Missing Authentication header', 'code': 401}}
⚡ Interrupted during API call.
```

The error is "**Missing Authentication header**" — not "Invalid API key" or "Quota exceeded." The request is being constructed without an `Authorization:` header at all.

### 2. How this produces the documented 320s / 11 api_calls / 0-char hang

The hermes-alignment.md:115 trace shows:
- An image-bearing inbound message (caption="expense") arrives
- Main client (`moonshotai/kimi-k2-thinking`) starts handling
- Main client invokes `vision_analyze` tool to extract image content
- `vision_analyze` calls auxiliary client which 401s
- Main client receives the tool failure, retries (model decides to retry the tool because it's blocked from producing a useful response without it)
- 11 tool-call attempts × ~30s OpenAI default retry timeout ≈ 320s
- Eventually the main client gives up with empty response (no useful output from the broken tool path)
- `dispatcher_routed` audit entry never written because dispatcher SKILL never gets invoked — the main client never finishes "preparing context" to dispatch

This unifies the two observations: the 320s symptom is downstream of the 401 root cause.

### 3. Auth chain currently configured

```yaml
# /root/.hermes/config.yaml
model:
  default: moonshotai/kimi-k2-thinking
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
auxiliary:
  vision:
    provider: openrouter
    model: openai/gpt-4o-mini
```

`hermes config show` reports `OpenRouter sk-o...cf22` is configured. So a key is present. The bug is in the **request construction path** for the vision auxiliary — the api_key isn't being injected into the Authorization header at the `client.chat.completions.create` call site (auxiliary_client.py:3708).

Likely causes (not exhaustive):
- The auxiliary vision client is instantiated with `api_key=None` because the explicit `provider: openrouter` block doesn't inherit the main provider's credentials the same way `provider: "auto"` would
- Hermes auxiliary-client credential resolution diverges from main-client behavior when `auxiliary.vision.provider` is set explicitly

### 4. Systemd journal (the secondary finding)

The `journalctl -u hermes-gateway --since "2026-05-01"` output shows **two separate failure patterns** that are NOT the documented hang:

- **16:21–16:38 cluster:** ~12 service restarts with `code=exited, status=1/FAILURE`. Each process exits within 1–3 seconds CPU time. These are **CLI startup failures**, not in-process hangs.
- **17:55–17:56 cluster:** ~4 restarts with `code=exited, status=2/INVALIDARGUMENT`.

The application log captures the cause: `hermes: error: unrecognized arguments: --yolo`. The `--yolo` flag is shown in `hermes --help` usage but is rejected at runtime. The systemd ExecStart is `python -m hermes_cli.main gateway run --replace` — no `--yolo` there. So the `--yolo` errors come from a different process invocation (cron job, watchdog, or operator script) that's incompatible with deployed Hermes 0.12.0, leaking stderr into the shared application log.

The 1h17m successful run between 16:38:09 → 17:54:48 (consumed 44.7s CPU, 475MB memory peak) is most likely the window during which the documented 320s hang(s) occurred.

### 5. Bridge code -15 exits

The hermes-alignment.md:115 entry mentions "Bridge also exited code -15 ~6 times same day." In Linux signal terms, exit code -15 means SIGTERM (signal 15). The bridge being SIGTERM'd is consistent with hermes-gateway service restarts during the 16:21–16:38 cluster — the bridge runs inside the gateway process, so when systemd kills the gateway, the bridge subprocess receives SIGTERM. Not a separate failure mode.

## Implications for the model-switch decision (P2.5)

| Hypothesis (before this diagnosis) | Truth (after diagnosis) |
|---|---|
| k2-thinking has reasoning+tool-use interaction failure → swap to gpt-4o-mini fixes it | False. Vision aux 401 is the upstream cause; main model is innocent. |
| 320s hangs are model-side | False. They're auth-error retry loops surfaced through tool-call thrashing. |
| Cost-driven model swap will improve reliability | Partially false. It might mask the symptom by making the model retry less aggressively, but the underlying 401 will still corrupt vision-bearing inbound flows. |
| Dispatcher-replay harness validates the swap | Still useful — but doesn't catch this issue because the harness will replay text-only `raw_inbound` entries, while the 401 only fires on image inputs. |

## Recommended actions (NOT executed in this diagnosis pass)

In priority order:

1. **Fix the vision auth issue.** Three approaches to try:
   - (a) Change `auxiliary.vision.provider` from `openrouter` to `auto` and let Hermes resolve credentials from the main provider config. Test on srilu-vps first.
   - (b) Add explicit `api_key: ${OPENROUTER_API_KEY}` to the `auxiliary.vision` block.
   - (c) Inspect Hermes upstream `auxiliary_client.py:3708` request-construction path; this may be a Hermes-side bug worth filing upstream.

2. **Add a vision-pipeline smoke test** to the deploy gate (already in `tasks/todo.md` P1: "Auxiliary vision pipeline test — synthetic image upload through the bridge stub, assert pending file gets created within N seconds"). Would have caught this regression at deploy time.

3. **Investigate the `--yolo` invoker.** Find which cron job or script is launching `hermes ... --yolo` so it stops polluting the application log and confusing future diagnoses. Check `systemctl list-timers`, `crontab -l`, watchdog scripts in `/etc/systemd/system/*.service`.

4. **Once vision auth is fixed,** re-evaluate whether the hangs return. If they don't, the model-swap decision can proceed on cost/quality grounds alone — not as a reliability fix.

5. **If hangs still occur after vision fix:** then we have a genuine main-client issue and the dispatcher-replay harness becomes the right validation tool.

## What this does NOT change

- Per-skill model routing still doesn't exist in Hermes 0.12.0 (`reference_hermes_model_routing.md`).
- B+step4 path is still the right cost-savings strategy (`project_model_strategy.md`).
- Layer C dispatcher-replay harness is still worth building for routing-decision validation independent of this diagnosis.

What it DOES change: the *urgency* of fixing the vision auth issue moves above the model-swap discussion, and step 4 should not be flipped until vision auth is verified working — otherwise we'd be evaluating gpt-4o-mini's dispatcher quality in a context where vision-bearing inputs are still broken upstream.

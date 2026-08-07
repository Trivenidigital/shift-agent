#!/usr/bin/env python3
"""Prove the startup gate REFUSES (non-zero exit) when deliberately broken.

Three deliberate breakages, each restored immediately:
  1. plugin removed from plugins.enabled   -> B and D must fail
  2. screening import broken               -> A (and the adapter factory) must fail
  3. healthy baseline                      -> must PASS (guards against a gate
                                              that refuses unconditionally)

config.yaml is backed up in memory and restored in a finally block; the script
verifies byte-identical restoration before exiting.
"""
import os
import subprocess
import sys

CONFIG = "/root/.hermes/config.yaml"
PREFLIGHT = "/usr/local/bin/shift-agent-policy-preflight"
PY = "/root/.hermes/hermes-agent/venv/bin/python"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def run_preflight(extra_env=None, prelude=None):
    """Run the REAL preflight script. `prelude` lets us poison sys.modules first
    while still executing the real file via runpy."""
    env = dict(os.environ)
    env["HERMES_HOME"] = "/root/.hermes"
    if extra_env:
        env.update(extra_env)
    if prelude:
        code = prelude + (
            "\nimport runpy\n"
            f"runpy.run_path({PREFLIGHT!r}, run_name='__main__')\n"
        )
        cmd = [PY, "-c", code]
    else:
        cmd = [PY, PREFLIGHT]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
    return p.returncode, (p.stdout + p.stderr)


original = open(CONFIG, encoding="utf-8").read()
try:
    print("=== 0. healthy baseline must PASS ===")
    rc, out = run_preflight()
    check("healthy config -> preflight exits 0", rc == 0, f"rc={rc}")
    check("healthy config -> reports screened adapter",
          "ScreenedWhatsAppAdapter" in out)

    print("\n=== 1. plugin NOT in plugins.enabled -> must REFUSE ===")
    broken = original.replace("  - shift-agent-policy\n", "")
    assert broken != original, "could not remove the enabled entry"
    open(CONFIG, "w", encoding="utf-8").write(broken)
    rc, out = run_preflight()
    check("disabled plugin -> preflight exits NON-ZERO", rc != 0, f"rc={rc}")
    check("disabled plugin -> B failure reported (not enabled)",
          "REFUSE  B" in out, [l for l in out.splitlines() if "REFUSE" in l][:1])
    check("disabled plugin -> D failure names the UNSCREENED class",
          "REFUSE  D" in out and "UNSCREENED" in out,
          [l for l in out.splitlines() if "REFUSE  D" in l][:1])

    open(CONFIG, "w", encoding="utf-8").write(original)
    print("    (config restored)")

    print("\n=== 2. screening import broken -> must REFUSE ===")
    prelude = (
        "import sys, types\n"
        # a safe_io module WITHOUT the screen symbol -> ImportError on use
        "sys.modules['safe_io'] = types.ModuleType('safe_io')\n"
    )
    rc, out = run_preflight(prelude=prelude)
    check("broken screen -> preflight exits NON-ZERO", rc != 0, f"rc={rc}")
    check("broken screen -> refusal mentions the front-brain screen",
          "REFUSE" in out and ("front-brain screen unimportable" in out
                              or "front_brain_screen_gateway_send" in out),
          [l for l in out.splitlines() if "REFUSE" in l][:2])
    check("broken screen -> refusal message states it will not relay unscreened",
          "rather than relay unscreened" in out)

finally:
    open(CONFIG, "w", encoding="utf-8").write(original)
    restored = open(CONFIG, encoding="utf-8").read()
    check("config.yaml restored byte-identically", restored == original)

print()
failed = [n for n, ok in RESULTS if not ok]
print(f"TOTAL {len(RESULTS)} checks, {len(RESULTS) - len(failed)} pass, {len(failed)} fail")
if failed:
    for n in failed:
        print("  FAILED:", n)
    sys.exit(1)
print("STARTUP GATE PROVEN: refuses when broken, passes when healthy")

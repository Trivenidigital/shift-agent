---
name: org-shared-runtime-effective-diagnosis
description: "Diagnose runtime state from the consuming process, never from a path, config key, or doc."
version: 0.1.0-stage-a
---
# Runtime-effective diagnosis (STAGE A — NOT FOR PRODUCTION)

## Evidence hierarchy — strongest first
1. active consuming process
2. resolved executable and environment (`/proc/<pid>/exe`, `/proc/<pid>/environ`, cmdline)
3. service definition that launched it (`ExecStart`)
4. runtime-effective configuration and state
5. filesystem / configuration evidence
6. documentation

## Hard rules
- NEVER infer the active version from an installation directory that may be unused.
- NEVER infer the active HERMES_HOME from the invoking shell's default.
- NEVER infer reachability from enabled metadata or a config-key name alone.
- Label any filesystem-only or documentation-only finding **PROVISIONAL**.
- Every material conclusion MUST link to the consuming runtime process.
- If runtime evidence is absent, answer **INCONCLUSIVE** and list the exact commands needed.

## Output format
```
verdict: CONFIRMED | PROVISIONAL | INCONCLUSIVE
evidence_tier_used: <1-6>
required_runtime_evidence: [<commands>]
reasoning: <short>
```

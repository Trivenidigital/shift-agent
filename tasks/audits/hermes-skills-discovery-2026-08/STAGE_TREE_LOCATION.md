# Stage tree — deliberately outside git

The `structured-stage-a/` tree (~52 MB, 301 PNGs, 158 `.py`) referenced throughout
`BOUNDED_AUTONOMOUS_SESSION_REPORT.md` is **intentionally not tracked in this repository**.

## Why it is not here

Its untracked-ness was load-bearing, not incidental. Per `tasks/lessons.md` (2026-08-03):

> An independent author needs enforceable information separation, not a different prompt.
> The stage tree being untracked in git made worktree isolation genuinely enforceable.

Committing it would durably preserve the bytes while destroying the property that made the
independence claim credible. It would also add ~52 MB to repo history permanently.

## Where it is

As of 2026-08-06 the tree exists at:

```
C:\projects\sme-agents\tasks\audits\hermes-skills-discovery-2026-08\structured-stage-a\
```

on the build machine only — **one machine, one working tree, no backup.** This is a known
durability gap, recorded here rather than silently accepted. It has not been archived because
no archive location has been authorized.

## What it contains that the closure record says survives

All four verified present on disk 2026-08-06:

| Artifact | Path under `structured-stage-a/` |
|---|---|
| P4 deterministic renderer `render-1` | `stage-b/model-adapter/workflows.py` (byte-equality gate in `stage-b/hostlib/validators.py`) |
| 84 integrity-verified PNGs + manifest | `stage-b/visual/p5_holdout_v4/` |
| `preflight-4` (14 steps) | `stage-b/hostlib/preflight.py` |
| `gate-registry-2` | `stage-b/hostlib/gate_registry.py` |
| P6 operation split `p6_operations_v5` | `stage-b/hostlib/p6_operations.py` |

**Reuse of any of these requires explicit authorization under a NEW programme.** The programme
that produced them is terminally closed — see the closure block at the top of `tasks/todo.md`.
Four known open harness defects must be fixed before reuse; they are listed in that same block.

Note in particular: **P5's authoring succeeded and its harness failed.** The 84 PNGs are real and
reusable. Do not re-author them.

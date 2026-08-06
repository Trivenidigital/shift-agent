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

on the build machine only — one machine, one working tree.

**The accepted evidence within it is no longer at risk.** The 84 accepted P5 holdout-v4 PNGs are
preserved in a committed archive in this directory (see below). The rest of the tree — generators,
harness source, rejected and intermediate PNGs, caches — remains build-machine-only by design, and
is reproducible or superseded rather than irreplaceable.

## Durable archive of the accepted evidence

| field | value |
|---|---|
| filename | `p5_holdout_v4_accepted_evidence.tar.gz` |
| SHA-256 | `f280273c5a5edcf7ce8ccc07977c63bc513e0e324a69e42bb33617cf83ba83a7` |
| bytes | `2636981` |
| entries | 87 — 84 PNGs + `MANIFEST.json` + `README.md` + `p5_holdout_v4_manifest.json` |
| PNG payload | `3247681` bytes across 84 files |
| roles | 54 `delivered_edited`, 25 `approved_after`, 5 `layout_reference` |
| storage | committed to this repository — retrievable offline from any clone, no credentials |
| per-file manifest | `p5_holdout_v4_accepted_evidence.MANIFEST.json` (committed alongside, so the 84 hashes are readable without unpacking) |

### Creation command

```sh
python p5_holdout_v4_archive_build.py \
  <path-to>/structured-stage-a/stage-b/visual/p5_holdout_v4 \
  <output-dir>
```

The builder is committed in this directory. It is deterministic — sorted entry order, uid/gid 0,
empty uname/gname, all entry mtimes 0, gzip mtime 0 — so re-running it on the same inputs
reproduces the identical SHA-256 above. Verified 2026-08-06: an independent rebuild produced a
byte-identical archive. It aborts rather than proceeding if any PNG fails its authoring hash or if
the accepted set is not exactly 84 files.

### Verification command

```sh
sha256sum p5_holdout_v4_accepted_evidence.tar.gz
# expect f280273c5a5edcf7ce8ccc07977c63bc513e0e324a69e42bb33617cf83ba83a7

tar xzf p5_holdout_v4_accepted_evidence.tar.gz
cd p5_holdout_v4_accepted_evidence
python - <<'PY'
import json, hashlib, pathlib
m = json.load(open('MANIFEST.json'))
bad = [e['relative_path'] for e in m['files']
       if hashlib.sha256(pathlib.Path(e['relative_path']).read_bytes()).hexdigest() != e['sha256']]
print(f"{len(m['files']) - len(bad)}/{len(m['files'])} verified", "FAIL: " + str(bad) if bad else "OK")
PY
```

### Integrity chain

Each of the 84 PNGs was verified against the SHA-256 recorded in the **original authoring
manifest** (`p5_holdout_v4_manifest.json`, written at authoring time), not merely self-hashed at
archive time. 84/84 matched, with no missing and no extra files. The archived copies were then
confirmed byte-identical to the source files. No PNG was regenerated, recompressed, resized or
renamed.

Evidence roles are derived from the authoring manifest's structure —
`layouts[].reference_file`, `cases[].edited_file`, `cases[].authorized_changes[].approved_after_file`
— not from filename parsing. All 84 are reachable from that structure; the builder aborts if any
file is not.

Note on the 25 `approved_after` files: the closure record states that completeness against
*intended* approved-after coverage is **unmeasurable**, because the frozen target specified zero
`approved_after_file` entries. The archive preserves what was authored. It does not, and cannot,
assert that 25 is the correct number — that is open harness defect #4.

## What it contains that the closure record says survives

All four verified present on disk 2026-08-06:

| Artifact | Path under `structured-stage-a/` |
|---|---|
| P4 deterministic renderer `render-1` | `stage-b/model-adapter/workflows.py` (byte-equality gate in `stage-b/hostlib/validators.py`) |
| 84 integrity-verified PNGs + manifest | `stage-b/visual/p5_holdout_v4/` — **also preserved in the committed archive above; this is the one artifact that no longer depends on the build machine** |
| `preflight-4` (14 steps) | `stage-b/hostlib/preflight.py` |
| `gate-registry-2` | `stage-b/hostlib/gate_registry.py` |
| P6 operation split `p6_operations_v5` | `stage-b/hostlib/p6_operations.py` |

**Reuse of any of these requires explicit authorization under a NEW programme.** The programme
that produced them is terminally closed — see the closure block at the top of `tasks/todo.md`.
Four known open harness defects must be fixed before reuse; they are listed in that same block.

Note in particular: **P5's authoring succeeded and its harness failed.** The 84 PNGs are real and
reusable. Do not re-author them.

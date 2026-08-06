"""Rebuild the P5 holdout-v4 accepted-evidence archive, deterministically.

Usage:
    python p5_holdout_v4_archive_build.py <SRC_p5_holdout_v4_dir> [OUT_DIR]

Deterministic by construction: entry order sorted, uid/gid 0, empty uname/gname,
all entry mtimes 0, gzip mtime 0. Rebuilding from the same inputs reproduces a
byte-identical archive with SHA-256:

    f280273c5a5edcf7ce8ccc07977c63bc513e0e324a69e42bb33617cf83ba83a7

Does NOT regenerate, recompress, resize or rename any PNG. Files are copied
byte-for-byte and every one is verified against the authoring manifest's
SHA-256 before being added; any mismatch aborts.
"""
import json, hashlib, io, os, sys, gzip, tarfile

SRC = sys.argv[1] if len(sys.argv) > 1 else os.environ['P5_SRC']
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
ARCHIVE_NAME = 'p5_holdout_v4_accepted_evidence.tar.gz'

os.makedirs(OUT_DIR, exist_ok=True)
src_manifest = json.load(open(os.path.join(SRC, 'p5_holdout_v4_manifest.json')))
files = src_manifest['files']

# ---- authoritative role/case map, derived from cases+layouts (not filenames) ----
role = {}
for lay in src_manifest['layouts']:
    role[lay['reference_file']] = {
        'evidence_role': 'layout_reference',
        'layout_id': lay['layout_id'],
        'case_id': None,
    }
for case in src_manifest['cases']:
    role[case['edited_file']] = {
        'evidence_role': 'delivered_edited',
        'layout_id': case['layout_id'],
        'case_id': case['case_id'],
    }
    for ch in case.get('authorized_changes', []):
        f = ch.get('approved_after_file')
        if f:
            role[f] = {
                'evidence_role': 'approved_after',
                'layout_id': case['layout_id'],
                'case_id': case['case_id'],
            }

unmapped = sorted(set(files) - set(role))
if unmapped:
    raise SystemExit(f'ABORT: {len(unmapped)} file(s) not reachable from cases/layouts: {unmapped}')

# ---- verify against authoring manifest, collect bytes ----
payload = {}
for name in sorted(files):
    data = open(os.path.join(SRC, name), 'rb').read()
    got = hashlib.sha256(data).hexdigest()
    exp = files[name]['sha256']
    if got != exp:
        raise SystemExit(f'ABORT: sha256 mismatch for {name}: expected {exp} got {got}')
    payload[name] = data

if len(payload) != 84:
    raise SystemExit(f'ABORT: expected 84 accepted PNGs, found {len(payload)}')

# ---- MANIFEST.json ----
entries = []
for name in sorted(payload):
    r = role[name]
    entries.append({
        'relative_path': f'p5_holdout_v4/{name}',
        'sha256': files[name]['sha256'],
        'bytes': len(payload[name]),
        'layout_id': r['layout_id'],
        'case_id': r['case_id'],
        'evidence_role': r['evidence_role'],
        'width': files[name]['width'],
        'height': files[name]['height'],
    })

manifest = {
    'archive': ARCHIVE_NAME,
    'holdout_id': src_manifest['holdout_id'],
    'target': src_manifest['target'],
    'case_id_namespace': src_manifest['case_id_namespace'],
    'programme': 'Hermes structured-output skills programme (P1/P3/P4/P5/P6)',
    'programme_status': 'TERMINALLY CLOSED 2026-08-03 — all five REJECT_AND_RETIRE',
    'preserved_because': (
        'P5 authoring succeeded and its harness failed. These 84 PNGs are '
        'integrity-verified and reusable; the closure record prohibits re-authoring them.'
    ),
    'reuse_condition': 'Requires explicit authorization under a NEW programme.',
    'entry_count_png': len(entries),
    'total_png_bytes': sum(e['bytes'] for e in entries),
    'integrity': 'all 84 SHA-256 verified against the original authoring manifest',
    'files': entries,
}
manifest_bytes = json.dumps(manifest, indent=2, sort_keys=False).encode('utf-8')

readme = f'''# P5 holdout-v4 — accepted evidence archive

84 integrity-verified PNGs from the **P5 holdout-v4** package (`{src_manifest['holdout_id']}`,
frozen target `{src_manifest['target']}`).

## Why this archive exists

The Hermes structured-output skills programme (P1/P3/P4/P5/P6) is **terminally closed** as of
2026-08-03 — all five workflows `REJECT_AND_RETIRE`. For P5 specifically: **the authoring
succeeded and the harness failed.** The mandatory preflight was structurally invalid before
case 1, so 0 of 54 cases executed. The PNGs themselves are sound.

The closure record explicitly says these must not be re-authored. This archive is their
durable copy, because the source tree they live in is deliberately untracked in git.

## Contents

- `p5_holdout_v4/` — the 84 accepted PNGs, byte-for-byte, unmodified
- `MANIFEST.json` — per-file relative path, SHA-256, byte size, layout, case, evidence role
- `p5_holdout_v4_manifest.json` — original authoring index: 5 layouts, 54 cases, 84 files
- `README.md` — this file

Nothing else. No generator scripts, caches, `.pyc`, rejected or intermediate PNGs.

## Evidence roles

| role | count | meaning |
|---|---|---|
| `layout_reference` | {sum(1 for e in entries if e['evidence_role']=='layout_reference')} | clean reference sheet for a layout |
| `delivered_edited` | {sum(1 for e in entries if e['evidence_role']=='delivered_edited')} | the delivered/edited sheet under adjudication |
| `approved_after` | {sum(1 for e in entries if e['evidence_role']=='approved_after')} | approved-after sheet an authorized change points to |

Roles are derived from the authoring manifest's `layouts[].reference_file`,
`cases[].edited_file` and `cases[].authorized_changes[].approved_after_file` — not from
filename parsing. Every one of the 84 is reachable from that structure.

## Verify

```sh
sha256sum {ARCHIVE_NAME}          # compare to the recorded archive hash
tar xzf {ARCHIVE_NAME}
cd p5_holdout_v4_accepted_evidence
python - <<'PY'
import json, hashlib, pathlib
m = json.load(open('MANIFEST.json'))
bad = [e['relative_path'] for e in m['files']
       if hashlib.sha256(pathlib.Path(e['relative_path']).read_bytes()).hexdigest() != e['sha256']]
print(f"{{len(m['files']) - len(bad)}}/{{len(m['files'])}} verified", "FAIL: " + str(bad) if bad else "OK")
PY
```

## Reuse

**Requires explicit authorization under a NEW programme.** The programme that produced these is
closed. Four known harness defects must be fixed before any reuse — see the closure block at the
top of `tasks/todo.md`.

## Determinism

Archive built with sorted entry order, uid/gid 0, empty uname/gname, all entry mtimes 0 and gzip
mtime 0. Rebuilding from the same inputs reproduces a byte-identical archive and SHA-256.
'''.encode('utf-8')

# ---- deterministic tar.gz ----
ROOT = 'p5_holdout_v4_accepted_evidence'


def ti(name, data):
    t = tarfile.TarInfo(f'{ROOT}/{name}')
    t.size = len(data)
    t.mtime = 0
    t.mode = 0o644
    t.uid = t.gid = 0
    t.uname = t.gname = ''
    t.type = tarfile.REGTYPE
    return t


raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as tar:
    tar.addfile(ti('README.md', readme), io.BytesIO(readme))
    tar.addfile(ti('MANIFEST.json', manifest_bytes), io.BytesIO(manifest_bytes))
    idx = json.dumps(src_manifest, indent=2).encode('utf-8')
    tar.addfile(ti('p5_holdout_v4_manifest.json', idx), io.BytesIO(idx))
    for name in sorted(payload):
        d = payload[name]
        tar.addfile(ti(f'p5_holdout_v4/{name}', d), io.BytesIO(d))

gz = io.BytesIO()
with gzip.GzipFile(fileobj=gz, mode='wb', compresslevel=9, mtime=0) as f:
    f.write(raw.getvalue())
blob = gz.getvalue()

out = os.path.join(OUT_DIR, ARCHIVE_NAME)
open(out, 'wb').write(blob)

print('archive      :', ARCHIVE_NAME)
print('sha256       :', hashlib.sha256(blob).hexdigest())
print('bytes        :', len(blob))
print('png entries  :', len(entries))
print('total entries:', len(entries) + 3)
print('png bytes    :', sum(e['bytes'] for e in entries))
print('path         :', out)

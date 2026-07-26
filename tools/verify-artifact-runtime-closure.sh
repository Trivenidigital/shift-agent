#!/usr/bin/env bash
# verify-artifact-runtime-closure — deploy-time fail-closed gate proving the flat
# /opt/shift-agent runtime is CLOSED over the approved deployment artifact.
#
# WHY (F-8 / DGT-21 / SP-GO-10, 2026-07-26 review): the flat runtime layout means
# live code imports modules by flat name (e.g. flyer_brief_validator.py does
# `from creative_firewall import ...`). When an install target is renamed, the OLD
# flat name can linger as a stale ORPHAN the installer never refreshes. That is
# exactly the 2026-06-05 incident: the installer wrote only the flyer_-prefixed
# `flyer_creative_firewall.py`, so the bare `creative_firewall.py` the code actually
# loads was never refreshed and a stale 2026-06-05 copy survived across deploys —
# the deployed runtime was NOT byte-identical to the approved release.
#
# This gate re-derives the whole check FROM THE ARTIFACT — no hand-maintained module
# list that can drift — and fails closed on any of:
#   * stale / orphan  : a flat runtime module whose bytes are in NO artifact file
#                       (installer did not refresh it from this release);
#   * missing import  : a flat module imported by deployed code, provided by the
#                       artifact, but not laid down at /opt/shift-agent/<name>.py;
#   * stale import    : an imported flat dependency present but not byte-identical
#                       to the artifact's copy.
#
# Design — content-addressed, name-independent: the acceptable-content universe is
# the sha256 set of every *.py in the artifact tree. A correctly-installed flat
# module is a verbatim `install`-copy of an artifact source, so its sha256 is in
# that set REGARDLESS of the source->flat NAME mapping (flyer_ prefix, renames,
# etc.). Only a stale copy or an abandoned orphan has bytes present in NO artifact
# file. This needs no knowledge of the irregular naming and cannot drift.
#
# Scope: the top-level flat modules `<runtime>/*.py` (the layout the incident lives
# in). Subpackages (commerce/) and skills are installed + gated separately.
#
# Usage:  verify-artifact-runtime-closure.sh <artifact_src_root> <runtime_dir>
#   artifact_src_root : extracted deploy artifact (contains src/, tools/) — $STAGING
#   runtime_dir       : the flat runtime dir — /opt/shift-agent
#
# Pure filesystem: bash + coreutils (sha256sum, find, grep, sed, awk). No Python,
# no venv, no network — so the regression test can call it directly in a temp dir.
# Rollback-safe by construction: the installer WARN-skips this gate when a pre-gate
# rollback tarball does not ship the helper.
set -euo pipefail

# Usage errors exit 2 (helper misuse); closure violations exit 1 (fail-closed →
# the installer rolls back). The deploy gate treats any nonzero as a failure.
if [ "$#" -ne 2 ]; then
    echo "CLOSURE-FAIL[usage]: usage: verify-artifact-runtime-closure.sh <artifact_src_root> <runtime_dir>" >&2
    exit 2
fi
ARTIFACT_ROOT="$1"
RUNTIME_DIR="$2"

if [ ! -d "$ARTIFACT_ROOT" ]; then
    echo "CLOSURE-FAIL[usage]: artifact_src_root '$ARTIFACT_ROOT' is not a directory." >&2
    exit 2
fi
if [ ! -d "$RUNTIME_DIR" ]; then
    echo "CLOSURE-FAIL[usage]: runtime_dir '$RUNTIME_DIR' is not a directory." >&2
    exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

_sha() { sha256sum "$1" | awk '{print $1}'; }

# ── Artifact index ─────────────────────────────────────────────────────────────
#   artifact_hashes    : every artifact *.py sha256 (the acceptable-content set)
#   artifact_basehash  : "<basename-no-ext>\t<sha256>" for the import-closure check
#   artifact_basenames : unique basenames the artifact provides as importable modules
: > "$TMP/artifact_hashes"
: > "$TMP/artifact_basehash"
while IFS= read -r -d '' f; do
    h="$(_sha "$f")"
    printf '%s\n' "$h" >> "$TMP/artifact_hashes"
    b="$(basename "$f" .py)"
    printf '%s\t%s\n' "$b" "$h" >> "$TMP/artifact_basehash"
done < <(find "$ARTIFACT_ROOT" -type f -name '*.py' -print0)
sort -u "$TMP/artifact_hashes" -o "$TMP/artifact_hashes"
cut -f1 "$TMP/artifact_basehash" | sort -u > "$TMP/artifact_basenames"

if [ ! -s "$TMP/artifact_hashes" ]; then
    echo "CLOSURE-FAIL[usage]: no *.py files found under artifact_src_root '$ARTIFACT_ROOT' — wrong path or empty artifact." >&2
    exit 2
fi

fail=0
n_flat=0
n_import=0

# ── Check A — refresh + no stale/orphan (content-addressed) ─────────────────────
# Every top-level flat runtime module must be byte-identical to SOME artifact file.
while IFS= read -r -d '' m; do
    n_flat=$((n_flat + 1))
    h="$(_sha "$m")"
    if ! grep -qxF "$h" "$TMP/artifact_hashes"; then
        echo "CLOSURE-FAIL[stale-or-orphan]: $(basename "$m") (sha256 ${h:0:12}...) is not byte-identical to any file in the approved artifact — the installer did not refresh this flat runtime copy from the release (stale copy or abandoned orphan)." >&2
        fail=1
    fi
done < <(find "$RUNTIME_DIR" -maxdepth 1 -type f -name '*.py' -print0)

# ── Check B — import-closure (imported flat deps resolve to a CURRENT artifact file)
# For every bare `import X` / `from X import` in a deployed flat module where the
# artifact provides an X.py, require <runtime>/X.py to exist AND match the artifact.
# Bare-name only (single identifier, no dot): dotted package-style fallbacks
# (`from agents.flyer.creative_firewall import ...`) resolve to a package, not a flat
# module, so they are intentionally out of scope. stdlib/site-package imports (re, os,
# yaml, pydantic, ...) are skipped because the artifact ships no same-named module.
while IFS= read -r -d '' m; do
    while IFS= read -r imp; do
        [ -n "$imp" ] || continue
        grep -qxF "$imp" "$TMP/artifact_basenames" || continue   # not an artifact-provided module
        n_import=$((n_import + 1))
        rt="$RUNTIME_DIR/$imp.py"
        if [ ! -f "$rt" ]; then
            echo "CLOSURE-FAIL[missing-import]: $(basename "$m") imports flat module '$imp', which the approved artifact provides, but the installer did not lay it down at $rt (missing flat dependency — the import would fail or silently resolve to a wrong module)." >&2
            fail=1
            continue
        fi
        rh="$(_sha "$rt")"
        if ! awk -F'\t' -v b="$imp" -v h="$rh" '$1==b && $2==h {ok=1} END{exit ok?0:1}' "$TMP/artifact_basehash"; then
            echo "CLOSURE-FAIL[stale-import]: $(basename "$m") imports flat module '$imp' but $rt (sha256 ${rh:0:12}...) is not byte-identical to the artifact's $imp.py — the imported dependency is stale." >&2
            fail=1
        fi
    done < <(grep -oE '^[[:space:]]*(from|import)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*' "$m" \
                | sed -E 's/^[[:space:]]*(from|import)[[:space:]]+//' | sort -u)
done < <(find "$RUNTIME_DIR" -maxdepth 1 -type f -name '*.py' -print0)

if [ "$fail" -ne 0 ]; then
    echo "CLOSURE-FAIL: artifact->runtime closure NOT satisfied (see CLOSURE-FAIL lines above)." >&2
    exit 1
fi

echo "CLOSURE-OK: ${n_flat} flat runtime modules byte-identical to the approved artifact; ${n_import} imported flat dependencies resolved + current."
exit 0

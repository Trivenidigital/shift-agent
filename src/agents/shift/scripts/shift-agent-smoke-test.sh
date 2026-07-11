#!/usr/bin/env bash
# shift-agent-smoke-test — verify deployment integrity.
# Runs after deploy. Does NOT send any outbound messages.
# Exit 0 = all checks pass; non-zero = deploy should be rolled back.

set -euo pipefail

# Use Hermes venv Python so pydantic + safe_io + schemas resolve. System
# Python (/usr/bin/python3) lacks pydantic, which would false-fail every
# import probe below.
PY="/usr/local/lib/hermes-agent/venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "FAIL: Hermes venv Python missing or not executable at $PY" >&2
    echo "  Hermes-agent install incomplete? Verify /usr/local/lib/hermes-agent/venv/" >&2
    exit 1
fi

echo "=== Shift Agent smoke test ==="

# 1. Scripts exist and are executable
for script in \
    /usr/local/bin/identify-sender \
    /usr/local/bin/log-decision \
    /usr/local/bin/log-decision-direct \
    /usr/local/bin/create-proposal \
    /usr/local/bin/handle-shift-sick-call \
    /usr/local/bin/update-proposal-status \
    /usr/local/bin/send-coverage-message \
    /usr/local/bin/render-coverage-template \
    /usr/local/bin/shift-agent-notify-owner \
    /usr/local/bin/shift-agent-disable \
    /usr/local/bin/shift-agent-enable \
    /usr/local/bin/shift-agent-hermes-permissions \
    /usr/local/bin/shift-agent-tail-logger.py \
    /usr/local/bin/shift-agent-health-check.sh \
    /usr/local/bin/shift-agent-reconcile.py \
    /usr/local/bin/send-routing-accuracy-summary \
    /usr/local/bin/lookup-prior-leads-by-phone \
    /usr/local/bin/create-catering-proposal-options \
    /usr/local/bin/select-catering-proposal \
    /usr/local/bin/create-flyer-project \
    /usr/local/bin/update-flyer-project \
    /usr/local/bin/check-flyer-reference-scope \
    /usr/local/bin/generate-flyer-concepts \
    /usr/local/bin/finalize-flyer-assets \
    /usr/local/bin/handle-flyer-onboarding \
    /usr/local/bin/handle-flyer-intake \
    /usr/local/bin/store-flyer-brand-asset \
    /usr/local/bin/manage-flyer-account \
    /usr/local/bin/manage-flyer-guest-order \
    /usr/local/bin/flyer-delivery-report \
    /usr/local/bin/flyer-recovery-watchdog \
    /usr/local/bin/flyer-recovery-preflight \
    /usr/local/bin/flyer-manual-queue \
    /usr/local/bin/flyer-source-edit-sla-watchdog \
    /usr/local/bin/flyer-intent-training-export \
    /usr/local/bin/send-flyer-campaign \
    /usr/local/bin/smoke-flyer-quality \
    /usr/local/bin/send-flyer-package ; do
    [ -x "$script" ] || { echo "FAIL: $script missing or not executable"; exit 1; }
done
echo "✓ All scripts present + executable"

if ! /usr/local/bin/shift-agent-hermes-permissions > /dev/null; then
    echo "FAIL: Hermes runtime permissions preflight failed"
    exit 1
fi
echo "✓ Hermes runtime permissions verified"

BRIDGE_JS="/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js"
if [ -f "$BRIDGE_JS" ]; then
    grep -q "app.post('/send-media'" "$BRIDGE_JS" || {
        echo "FAIL: WhatsApp bridge missing /send-media endpoint required for Flyer Studio delivery"
        exit 1
    }
    grep -q "app.post('/send-cta'" "$BRIDGE_JS" || {
        echo "FAIL: WhatsApp bridge missing /send-cta endpoint required for Flyer Studio campaign CTAs"
        exit 1
    }
    echo "✓ WhatsApp bridge exposes /send-media and /send-cta"
else
    echo "FAIL: WhatsApp bridge source not found at $BRIDGE_JS"
    exit 1
fi

# 2. Python modules importable + safe_io chokepoint symbols present
# Symbol list lives in src/platform/scripts/check-safe-io-symbols — single
# source of truth shared with shift-agent-deploy.sh pre-restart gate.
if ! "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import schemas, safe_io, exit_codes
import flyer_render
import flyer_workflow
import flyer_onboarding
import flyer_account
import flyer_starter_briefs
import flyer_recovery
import flyer_intake_fields
import flyer_bare_render
assert callable(flyer_recovery.classify_flyer_qa_for_autorepair), \
    'flyer autorepair classifier missing'
assert callable(flyer_recovery.plan_flyer_autorepair), \
    'flyer autorepair planner missing'
assert flyer_recovery.repair_instruction_is_safe('Show each offer item once.'), \
    'flyer autorepair safe-instruction guard missing'
import flyer_customer_copy_policy
import flyer_intent
import flyer_intent_training
import flyer_facts
import flyer_creative_firewall
import flyer_reference_extract
import flyer_semantic_brief
import flyer_visual_qa
import flyer_premium_overlay
# Premium Poster v1 stack (PR #523 + deploy-readiness): the flat modules must
# import on the box so render._render_model's premium branch can resolve its
# adapters when armed. Imports only — the branch stays a no-op while the flag is
# OFF (default). This is the on-box deploy smoke for files-exist + imports-resolve
# + premium-branch-initializes + flag-off-no-op.
import flyer_premium_poster_v1
import flyer_premium_poster_v1_director
import flyer_art_director_oracle
assert callable(flyer_premium_poster_v1.compose_premium_poster_v1), \
    'premium poster composer missing on box'
assert callable(flyer_premium_poster_v1_director.compose_best_of_n), \
    'premium poster best-of-N selector missing on box'
assert callable(flyer_art_director_oracle.score_art_direction), \
    'art-director oracle missing on box'
assert callable(flyer_render.render_premium_poster_v1), \
    'render premium branch missing on box'
assert flyer_render._premium_poster_v1_armed(
    type('P', (), {'customer_phone': '+10000000000'})()) is False, \
    'premium poster gate must never arm a non-allowlisted sender'
import flyer_manual_queue
# PR-ζ.1b 2026-05-26 — verify flat-renamed allowlist entry matches the
# deployed basename, verify stale entry removed, verify cf-router entries
# removed (commit 8), verify PROJECT_ACTIONS + helpers import.
assert 'flyer_manual_queue.py' in safe_io.SAFE_IO_NULL_CONTEXT_ALLOWLIST, \
    'PR-ζ.1b: flyer_manual_queue.py missing from allowlist'
assert 'manual_queue.py' not in safe_io.SAFE_IO_NULL_CONTEXT_ALLOWLIST, \
    'PR-ζ.1b: stale manual_queue.py still in allowlist'
import flyer_action_registry
assert 'change_plan_fallback' in flyer_action_registry.ACCOUNT_ACTIONS, \
    'PR-ζ.1b: change_plan_fallback missing from ACCOUNT_ACTIONS'
assert 'command_reply' in flyer_action_registry.ACCOUNT_ACTIONS, \
    'PR-ζ.1b: command_reply missing from ACCOUNT_ACTIONS'
assert flyer_action_registry.PROJECT_ACTIONS, 'PR-ζ.1b: PROJECT_ACTIONS empty'
_ = flyer_action_registry.build_action_context_for_command(
    flyer_action_registry.PROJECT_ACTIONS, 'intake.acknowledged',
)
# deterministic-recovery symbols + flag default (feat/flyer-deterministic-recovery-routing)
import os as _os
assert hasattr(flyer_render, '_deterministic_recovery_enabled'), \
    'flyer_render._deterministic_recovery_enabled missing — deterministic-recovery routing broken'
assert hasattr(flyer_visual_qa, 'is_own_brand_variant'), \
    'flyer_visual_qa.is_own_brand_variant missing — brand-typo gate broken'
assert _os.environ.get('FLYER_DETERMINISTIC_RECOVERY') in (None, '', '0', '1'), \
    'FLYER_DETERMINISTIC_RECOVERY has unexpected value — must be unset, empty, 0, or 1'
# CD v2 brain SKILL must be installed at the ACTUAL runtime read path with the
# CD v2 output schema (2026-06-21 stale-SKILL-path fix). flyer_context_builder
# (the Creative-Director brain) reads SKILL_MD_PATH = __file__.parent/skills/
# flyer_generation/SKILL.md = /opt/shift-agent/skills/flyer_generation/SKILL.md,
# NOT the Hermes dispatch copy under /root/.hermes/skills/. If the deploy doesn't
# refresh that path the brain reads a stale pre-CD-v2 SKILL and can never emit
# campaign_narrative/hero_ref/marketing_hook/offer_priority — the live render
# came out headline-less and the cause was mis-attributed to brain nondeterminism.
# Asserting via the brain's own SKILL_MD_PATH (not a hardcoded path) keeps this
# gate drift-proof against any future change to where the brain reads.
import flyer_context_builder as _fcb
_cdv2_skill = _fcb.SKILL_MD_PATH
assert _cdv2_skill.exists(), \
    'CD v2 brain SKILL absent at runtime read path %s (deploy did not install it)' % _cdv2_skill
_cdv2_body = _cdv2_skill.read_text(encoding='utf-8', errors='replace')
for _cdv2_field in ('campaign_narrative', 'hero_ref', 'marketing_hook', 'offer_priority'):
    assert _cdv2_field in _cdv2_body, \
        'CD v2 brain SKILL at %s is stale — missing field %r' % (_cdv2_skill, _cdv2_field)
# Resolver deploy-packaging gate: flyer_creative_resolver top-imports
# flyer_copy_archetypes; assert the resolver module loads from the flat path.
# smoke gate (not a live customer flyer at CD v2 narrative-selection time).
import flyer_creative_resolver  # noqa: F401 — top-imports flyer_copy_archetypes
# Controlled Copy Archetypes (CCA) deploy-packaging gate. flyer_creative_resolver
# top-imports compose_archetype_headlines from flyer_copy_archetypes; assert it loads so a
# missing install line fails this smoke gate (not a live customer flyer at compose time).
import flyer_copy_archetypes
assert callable(flyer_copy_archetypes.compose_archetype_headlines), \
    'flyer_copy_archetypes.compose_archetype_headlines missing — CCA deploy packaging broken'
print('schema classes:', [c for c in dir(schemas) if not c.startswith('_')][:5])
" > /dev/null; then
    echo "FAIL: Python modules don't import"
    exit 1
fi
# Wrap check-safe-io-symbols in "$PY" for the same reason as the other
# Python invocations: the script's #!/usr/bin/env python3 shebang would
# land on system Python, which lacks pydantic. Works today only because
# safe_io.py lazy-imports pydantic — guard against future changes.
if ! "$PY" /usr/local/bin/check-safe-io-symbols > /dev/null; then
    echo "FAIL: safe_io chokepoint symbols missing — run check-safe-io-symbols for details"
    exit 1
fi
echo "✓ Python modules importable (incl. safe_io chokepoint symbols)"
echo "✓ deterministic-recovery symbols present + flag default safe"

# 2.0a Fix C premium overlay — flat-import + font-bundle deploy gate.
# The premium renderer (FLYER_PREMIUM_OVERLAY=1) imports flyer_render /
# flyer_visual_qa / flyer_premium_overlay by their FLAT deployed names and
# loads vendored TTFs from premium_overlay._FONT_DIR. If the module didn't
# install under the flat name, or the fonts/ bundle is absent, the premium
# path silently degrades (or, pre-fix, dies on ImportError and falls back to
# legacy) — i.e. Fix C would never actually run in production. Assert both.
# premium_overlay imports CLEANLY without Pillow (PIL is lazy-imported only when
# rendering, like flyer_render), so the module-import + flat-import + font-FILE
# checks run under "$PY" (the Hermes venv: has pydantic, no Pillow). The font
# LOAD (which needs Pillow) is verified separately under the Pillow-capable
# python that actually renders flyers (/usr/bin/python3). Calling _premium_font
# under "$PY" would false-fail (ModuleNotFoundError: PIL).
if ! "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import flyer_premium_overlay as po
# Flat-name imports the renderer itself does at call time must resolve.
import flyer_render, flyer_visual_qa  # noqa: F401
# EVERY unique vendored role TTF must exist as a real file at the deployed
# _FONT_DIR (require ALL, not just one — a partial/incomplete bundle must fail).
unique = sorted(set(po._ROLE_FILES.values()))
missing = [fn for fn in unique if not (po._FONT_DIR / fn).exists()]
assert not missing, f'missing vendored premium TTFs at {po._FONT_DIR}: {missing}'
print(f'premium overlay flat-imports OK; all {len(unique)} vendored TTFs present at {po._FONT_DIR}')
" > /dev/null; then
    echo "FAIL: Fix C premium overlay flat-import or font-bundle missing — premium renderer would silently degrade"
    exit 1
fi
# Verify the vendored TTFs actually LOAD under the Pillow-capable render python.
# Best-effort: if Pillow is unavailable there, warn (matches the existing
# flyer-quality smoke's no-Pillow tolerance) rather than fail.
RENDER_PY=/usr/bin/python3
if "$RENDER_PY" -c "import PIL" 2>/dev/null; then
    if ! "$RENDER_PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import flyer_premium_overlay as po
from PIL import ImageFont
# Load each unique vendored TTF DIRECTLY (NOT via _premium_font, which would
# silently fall back to a system/default font on a corrupt/missing TTF and mask
# the failure). A bad or absent TTF must raise here.
for fn in sorted(set(po._ROLE_FILES.values())):
    ImageFont.truetype(str(po._FONT_DIR / fn), size=40)
print('all vendored premium TTFs load via ImageFont.truetype under', sys.executable)
" > /dev/null; then
        echo "FAIL: Fix C premium fonts present but fail to load under $RENDER_PY (corrupt/missing TTF?)"
        exit 1
    fi
    echo "✓ Fix C premium overlay imports flat + fonts present + load under $RENDER_PY"
else
    echo "⚠  Pillow absent under $RENDER_PY — premium font-LOAD check skipped (imports + bundle verified)"
fi

# 2.0b Fix C premium overlay — RENDER gate under the gateway venv interpreter.
# The gateway runs the flyer pipeline under a venv WITHOUT Pillow. After the
# flat-degrade fix, the premium overlay must still RENDER (via the /usr/bin/python3
# subprocess escape hatch) and report `premium_overlay_delivered` — NOT silently
# fall back to flat. Build a textless background with the PIL-capable render python,
# then drive _apply_critical_text_overlay under $PY and assert the recorded outcome.
# The smoke project mirrors tests/test_flyer_premium_overlay.py::_project6 (the
# known-good fixture proven by test_render_premium_overlay_writes_image) so a real
# render delivers (passes its own fit + coverage), rather than fail-closing to flat.
if "$RENDER_PY" -c "import PIL" 2>/dev/null; then
    SMOKE_DIR="$(mktemp -d)"
    BG="$SMOKE_DIR/bg.png"; OUT="$SMOKE_DIR/out.png"
    "$RENDER_PY" -c "
from PIL import Image
Image.new('RGB', (1080, 1350), (70, 40, 20)).save('$BG')
" > /dev/null 2>&1
    if ! FLYER_PREMIUM_OVERLAY=1 FLYER_PREMIUM_OVERLAY_ALLOWLIST= "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import flyer_render as r
from schemas import FlyerProject
# Mirror tests/test_flyer_premium_overlay.py::_project6 (known-good fixture).
facts = [
    {'fact_id':'business_name','label':'Business','value':\"Lakshmi's Kitchen\",'required':True,'source':'customer_text'},
    {'fact_id':'campaign_title','label':'Campaign','value':'Weekend Specials','required':True,'source':'customer_text'},
    {'fact_id':'contact_phone','label':'Contact','value':'+17329837841','required':True,'source':'customer_text'},
    {'fact_id':'location','label':'Location','value':'90 Brybar Dr St Johns FL','required':True,'source':'customer_text'},
    {'fact_id':'pricing_structure','label':'Pricing','value':'Any item \$7.99','required':True,'source':'customer_text'},
    {'fact_id':'schedule','label':'Schedule','value':'Saturday & Sunday, 4 PM-8 PM','required':True,'source':'customer_text'},
]
for i, n in enumerate(['Idli', 'Dosa', 'Vada', 'Uttapam', 'Pongal', 'Sambar']):
    facts.append({'fact_id':f'item:{i}:name','label':'Item','value':n,'required':True,'source':'customer_text'})
proj = FlyerProject.model_validate({
    'project_id':'F9001','status':'generating_concepts','customer_phone':'+17329837841',
    'customer_id':'CUST0001','created_at':'2026-06-18T00:00:00Z','updated_at':'2026-06-18T00:00:00Z',
    'original_message_id':'wamid.F9001',
    'raw_request':'Create a flyer for Weekend Specials. Any item \$7.99. Idli, Dosa, Vada, Uttapam, Pongal, Sambar. Sat & Sun 4-8 PM. +1 732-983-7841',
    'fields':{'event_or_business_name':'Weekend Specials','preferred_language':'en'},
    'locked_facts':facts,
})
# Smoke: force the premium/food path regardless of category heuristics or the
# customer allowlist scoping — we are testing that premium RENDERS under \$PY.
r._is_food_or_grocery_project = lambda p: True
r._premium_overlay_enabled = lambda p: True
r._apply_critical_text_overlay(proj, '$BG', '$OUT', size=(1080, 1350), output_format='concept_preview')
import importlib.util as _ilu
venv_has_pil = _ilu.find_spec('PIL') is not None
out = r.consume_premium_overlay_outcome()
assert out is not None, 'no premium outcome recorded (premium path did not run)'
assert out.status == 'premium_overlay_delivered', f'premium did NOT render under gateway venv: {out.status} ({out.reason_class}: {out.reason_detail})'
if not venv_has_pil:
    assert out.render_path == 'subprocess', f'expected /usr/bin/python3 escape hatch under PIL-less venv, got render_path={out.render_path}'
import os
assert os.path.getsize('$OUT') > 0, 'premium render produced an empty file'
print('premium renders premium under', sys.executable, 'via', out.render_path)
" > /dev/null; then
        echo "FAIL: premium overlay does NOT render premium under the gateway venv (\$PY) — would silently ship FLAT"
        rm -rf "$SMOKE_DIR"
        exit 1
    fi
    rm -rf "$SMOKE_DIR"
    echo "✓ premium overlay renders premium under gateway venv path (\$PY via subprocess)"
else
    echo "⚠  Pillow absent under \$RENDER_PY — premium RENDER gate skipped (subprocess escape hatch unverifiable here)"
fi

# 2.0c Deterministic-first routing gate. With FLYER_DETERMINISTIC_FIRST=1 a
# fact-dense food flyer must become integrated-INELIGIBLE (routes to the
# deterministic mode-2 overlay); with the flag unset it must stay eligible
# (byte-identical). Pure eligibility logic — no model call, no render.
if ! FLYER_ALLOW_INTEGRATED_POSTER=1 "$PY" -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
import os
import flyer_render as r
from schemas import FlyerProject
facts = [
    {'fact_id':'business_name','label':'B','value':\"Lakshmi's Kitchen\",'required':True,'source':'customer_text'},
    {'fact_id':'pricing_structure','label':'P','value':'Any item \$7.99','required':True,'source':'customer_text'},
]
for i, n in enumerate(['Idli','Dosa','Vada','Uttapam','Pongal','Sambar']):
    facts.append({'fact_id':f'item:{i}:name','label':'I','value':n,'required':True,'source':'customer_text'})
proj = FlyerProject.model_validate({
    'project_id':'F9002','status':'generating_concepts','customer_phone':'+17329837841',
    'customer_id':'CUST0001','created_at':'2026-06-20T00:00:00Z','updated_at':'2026-06-20T00:00:00Z',
    'original_message_id':'wamid.F9002','raw_request':'Weekend Specials menu any item \$7.99',
    'fields':{'event_or_business_name':'Weekend Specials','preferred_language':'en','notes':'menu'},
    'locked_facts':facts,
})
assert r._is_fact_dense(proj) is True, 'fact-dense classifier failed on a menu'
os.environ.pop('FLYER_DETERMINISTIC_FIRST', None)
os.environ.pop('FLYER_PREMIUM_OVERLAY_ALLOWLIST', None)
assert r._integrated_poster_eligible(proj) is True, 'flag-off should be byte-identical (integrated-eligible)'
os.environ['FLYER_DETERMINISTIC_FIRST'] = '1'
os.environ['FLYER_PREMIUM_OVERLAY_ALLOWLIST'] = '+17329837841'  # unified semantics: empty allowlist = DISABLED
assert r._integrated_poster_eligible(proj) is False, 'flag-on dense should route to mode 2 (ineligible for integrated)'
print('deterministic-first routing OK: dense+flag-on -> mode 2; flag-off unchanged')
" > /dev/null; then
    echo "FAIL: deterministic-first routing gate — dense flyer not routed to mode 2 under FLYER_DETERMINISTIC_FIRST, or flag-off not byte-identical"
    exit 1
fi
echo "✓ deterministic-first routing: fact-dense -> mode 2 under flag; no-op when off"

# 2a. Credential-minimized readiness report. Informational only: the strict
# external-foundation gate runs pre-install in shift-agent-deploy.sh, where a
# missing Hermes bundled skill can abort before app state changes. Post-restart
# smoke must not be the first strict check for external Hermes install state.
if ! sudo -u shift-agent "$PY" /usr/local/bin/smoke-flyer-quality --final-package > /dev/null; then
    echo "FAIL: Flyer quality deterministic smoke failed"
    exit 1
fi
echo "Flyer quality deterministic smoke passed"

OVERLAY_SMOKE_DIR="$(mktemp -d /tmp/flyer-overlay-smoke.XXXXXX)"
cleanup_overlay_smoke() { rm -rf "$OVERLAY_SMOKE_DIR"; }
trap cleanup_overlay_smoke EXIT
chown shift-agent:shift-agent "$OVERLAY_SMOKE_DIR"
if ! sudo -u shift-agent "$PY" - "$OVERLAY_SMOKE_DIR" <<'PY' > /dev/null
import base64
from datetime import datetime, timezone
from pathlib import Path
import sys

# Deploy-shape: /opt/shift-agent is not on sys.path by default for the
# shift-agent user's venv invocation. Other probes in this script (e.g. the
# Python modules importable block above) inject it explicitly; the overlay
# probe added in PR #298 missed this step and the new smoke gate
# correctly rolled back deploy 87db7154 at 2026-05-27T13:22Z. Pattern
# mirrors the PR-ζ #270 deployed-flat-module lesson: SSH pre-deploy
# runtime check beats review for deploy-shape correctness.
sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, "/opt/shift-agent/platform")

from flyer_render import apply_exact_identity_overlay
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

root = Path(sys.argv[1])
source = root / "source.png"
target = root / "target.png"
source.write_bytes(base64.b64decode(
    # Known-good 4x4 RGB PNG (77 bytes), generated by Pillow 10.2.0 on
    # main-vps 2026-05-27. The previous 1x1 fixture (68 bytes) had a
    # truncated/non-canonical IDAT chunk that Pillow 10.2.0's stricter
    # validator rejects with "broken data stream when reading image
    # file" at load time. Decision rule probe (operator-directed, run
    # against /usr/bin/python3 on main-vps): system Pillow OK on
    # generated PNG; FAIL on the prior fixture. Fixture-only fix; the
    # in-venv overlay code path is unchanged.
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR42mNcsOUEAwwwMSAB3BwAalQCJGH3RFYAAAAASUVORK5CYII="
))
now = datetime.now(timezone.utc)
project = FlyerProject(
    project_id="F0001",
    status="generating_concepts",
    customer_phone="+19045550123",
    created_at=now,
    updated_at=now,
    original_message_id="smoke-overlay",
    raw_request="Create a smoke flyer",
    fields=FlyerRequestFields(event_or_business_name="Smoke Overlay", contact_info="+19045550123"),
    locked_facts=[
        FlyerLockedFact(fact_id="business_name", label="Business", value="Smoke Kitchen", source="customer_profile"),
        FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+19045550123", source="customer_profile"),
    ],
)
apply_exact_identity_overlay(project, source, target, size=(1080, 1350))
if not target.exists() or target.stat().st_size <= 0:
    raise SystemExit("exact identity overlay did not produce output")
PY
then
    echo "FAIL: Flyer exact identity overlay smoke failed"
    exit 1
fi
echo "Flyer exact identity overlay smoke passed"
rm -rf "$OVERLAY_SMOKE_DIR"

REF_SMOKE_DIR="$(mktemp -d /tmp/flyer-reference-smoke.XXXXXX)"
cleanup_ref_smoke() { rm -rf "$REF_SMOKE_DIR"; }
trap cleanup_ref_smoke EXIT
mkdir -p "$REF_SMOKE_DIR/assets"
printf 'fake image bytes' > "$REF_SMOKE_DIR/menu.png"
cat > "$REF_SMOKE_DIR/config.yaml" <<'YAML'
schema_version: 1
customer:
  name: Smoke
  location_id: smoke
  timezone: America/New_York
owner:
  name: Owner
  phone: "+19045550000"
limits: {}
alerting:
  pushover_user_key: k
  pushover_app_token: t
backup:
  gpg_recipient_email: owner@example.com
flyer:
  enabled: true
  draft_image_model: deterministic-renderer
  draft_image_quality: low
  concept_count: 1
  recovery:
    auto_repair_enabled: true
    max_auto_repair_attempts: 1
YAML
chown -R shift-agent:shift-agent "$REF_SMOKE_DIR"
if ! sudo -u shift-agent env FLYER_STATE_ROOT="$REF_SMOKE_DIR" "$PY" /usr/local/bin/create-flyer-project \
    --customer-phone +19045550123 \
    --message-id smoke-reference-menu \
    --raw-request "Create a flyer for Smoke Menu. Contact +19045550123. Create a flyer from this attached menu." \
    --reference-media-path "$REF_SMOKE_DIR/menu.png" \
    --state-path "$REF_SMOKE_DIR/projects.json" \
    --customer-state-path "$REF_SMOKE_DIR/customers.json" \
    --asset-dir "$REF_SMOKE_DIR/assets" \
    --defer-reference-extraction > "$REF_SMOKE_DIR/create.json"; then
    echo "FAIL: Flyer deferred reference create smoke failed"
    exit 1
fi
REF_ASSET_PATH="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["assets"][0]["path"])' "$REF_SMOKE_DIR/create.json")"
printf 'Idly $7\nDosa $8\n' > "${REF_ASSET_PATH}.ocr.txt"
if ! sudo -u shift-agent env FLYER_STATE_ROOT="$REF_SMOKE_DIR" FLYER_REFERENCE_ALLOW_SIDECAR=1 FLYER_QA_ALLOW_SIDECAR=1 "$PY" /usr/local/bin/generate-flyer-concepts \
    --project-id F0001 \
    --state-path "$REF_SMOKE_DIR/projects.json" \
    --asset-dir "$REF_SMOKE_DIR/assets" \
    --config-path "$REF_SMOKE_DIR/config.yaml" \
    --audit-log-path "$REF_SMOKE_DIR/decisions.log" \
    --autorepair-state-path "$REF_SMOKE_DIR/autorepair_attempts.json" > "$REF_SMOKE_DIR/generate.json"; then
    echo "FAIL: Flyer deferred reference generate smoke failed"
    exit 1
fi
if ! "$PY" - "$REF_SMOKE_DIR/projects.json" <<'PY' > /dev/null; then
import json, sys
project = json.load(open(sys.argv[1], encoding="utf-8"))["projects"][0]
values = {fact["value"] for fact in project.get("locked_facts", [])}
assert {"Idly", "$7", "Dosa", "$8"}.issubset(values)
assert project["reference_extractions"][0]["status"] == "ok"
PY
    echo "FAIL: Flyer deferred reference facts smoke failed"
    exit 1
fi
trap - EXIT
cleanup_ref_smoke
echo "Flyer deferred reference extraction smoke passed"

# CD v2 durable rollback verification: after the deploy-time scrub, the flyer
# project store MUST contain zero `creative_direction` keys. The key is now
# Field(exclude=True) (never persisted by new code) and the deploy scrubs any
# lingering pre-fix keys before restart — so a surviving key here means the scrub
# step did not run / failed, which would make a future rollback to extra="forbid"
# code reject the store. Fail-closed (→ auto-rollback) if any remain. No-op when
# the store file is absent (fresh VPS / flyer never used).
FLYER_STORE_SMOKE=/opt/shift-agent/state/flyer/projects.json
if [ -f "$FLYER_STORE_SMOKE" ]; then
    if ! sudo -u shift-agent "$PY" - "$FLYER_STORE_SMOKE" <<'PY'; then
import json, sys
store = json.load(open(sys.argv[1], encoding="utf-8"))
projects = store.get("projects") if isinstance(store, dict) else None
leftover = sum(
    1 for p in (projects or [])
    if isinstance(p, dict) and "creative_direction" in p
)
if leftover:
    sys.stderr.write(f"{leftover} project(s) still carry creative_direction\n")
    raise SystemExit(1)
print("flyer store: 0 creative_direction keys (CD v2 rollback-safe)")
PY
        echo "FAIL: flyer project store still contains creative_direction keys post-scrub — CD v2 rollback safety broken"
        exit 1
    fi
    echo "✓ flyer project store has no creative_direction keys (CD v2 rollback-safe)"
else
    echo "⚠  flyer project store absent ($FLYER_STORE_SMOKE) — CD v2 scrub verification skipped (flyer unused)"
fi

if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-delivery-report --json > /dev/null; then
    echo "FAIL: Flyer delivery report failed"
    exit 1
fi
echo "Flyer delivery report smoke passed"

if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-recovery-watchdog --mode off --text > /dev/null; then
    echo "FAIL: Flyer recovery watchdog failed"
    exit 1
fi
if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-recovery-preflight --text > /dev/null; then
    echo "FAIL: Flyer recovery preflight failed"
    exit 1
fi
echo "Flyer recovery smoke passed"

if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-manual-queue --triage > /dev/null; then
    echo "FAIL: Flyer manual-queue triage view failed"
    exit 1
fi
echo "Flyer manual-queue triage smoke passed"

if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-source-edit-sla-watchdog --threshold-minutes 1000000 > /dev/null; then
    echo "FAIL: Flyer source-edit SLA watchdog advisory run failed"
    exit 1
fi
echo "Flyer source-edit SLA watchdog smoke passed"

if ! sudo -u shift-agent "$PY" /usr/local/bin/flyer-intent-training-export --help > /dev/null; then
    echo "FAIL: Flyer intent training export CLI failed"
    exit 1
fi
echo "Flyer intent training export CLI smoke passed"

if [ -x /usr/local/bin/credential-minimized-readiness ]; then
    "$PY" /usr/local/bin/credential-minimized-readiness --format text || true
fi

# 2a.1 Production-pilot readiness report. Informational only: customer
# onboarding data can intentionally be absent on rehearsal VPSes. This surfaces
# the blocking rows without making every non-onboarded deploy fail.
if [ -x /usr/local/bin/pilot-readiness-check ]; then
    "$PY" /usr/local/bin/pilot-readiness-check --text || true
fi

# 2b. cf-router plugin (PR-CF6 + PR-CF7) — verify the plugin's hooks +
# actions modules import cleanly and the F7 classifier is reachable.
# A syntax error or broken import in the plugin would otherwise pass
# all other checks and only manifest at first inbound traffic.
if [ -d /root/.hermes/plugins/cf-router ]; then
    if ! "$PY" - <<'PY' > /dev/null; then
from pathlib import Path
for p in [
    Path('/root/.hermes/plugins/cf-router/actions.py'),
    Path('/root/.hermes/plugins/cf-router/hooks.py'),
]:
    compile(p.read_text(), str(p), 'exec')
PY
        echo "FAIL: cf-router plugin actions.py/hooks.py compile check failed"
        exit 1
    fi
    if ! "$PY" -c "
import sys, importlib.util
sys.path.insert(0, '/opt/shift-agent')
spec_a = importlib.util.spec_from_file_location(
    'cf_router_smoke_actions',
    '/root/.hermes/plugins/cf-router/actions.py',
)
ma = importlib.util.module_from_spec(spec_a)
spec_a.loader.exec_module(ma)
# Sanity: classifier reachable + correct signature
ok, signals = ma.classify_catering('catering for 50 people event next Saturday food delivered')
assert ok is True, f'classifier regressed (positive case failed): signals={signals}'
ok2, _ = ma.classify_catering('hi')
assert ok2 is False, 'classifier regressed (too-short case)'
flyer_ok, flyer_signals = ma.classify_flyer_intent('Need flyer for Ugadi Specials with food style')
assert flyer_ok is True, f'flyer classifier regressed: signals={flyer_signals}'
generic_flyer_ok, _ = ma.classify_flyer_intent('Need catering for 80 people event Saturday food delivered')
assert generic_flyer_ok is False, 'flyer classifier stole generic catering'
assert hasattr(ma, 'begin_flyer_intent_shadow')
assert hasattr(ma, 'finalize_flyer_intent_shadow')
print('cf-router plugin: actions.py importable + classifiers OK')
" > /dev/null; then
        echo "FAIL: cf-router plugin actions.py broken — would silently fail at first inbound"
        exit 1
    fi
    echo "✓ cf-router plugin compiles + actions importable + classifier sanity"
else
    echo "⚠  cf-router plugin not installed — skipping plugin smoke check"
fi

if [ -d /root/.hermes/plugins/cf-router ]; then
    INTENT_SMOKE_DIR="$(mktemp -d /tmp/flyer-intent-smoke.XXXXXX)"
    if ! "$PY" - "$INTENT_SMOKE_DIR/decisions.log" <<'PY' > /dev/null; then
import importlib.util
import json
import sys
from pathlib import Path
from pydantic import TypeAdapter

sys.path.insert(0, '/opt/shift-agent')
from schemas import LogEntry  # noqa: E402

spec_a = importlib.util.spec_from_file_location(
    'cf_router_smoke_actions_intent',
    '/root/.hermes/plugins/cf-router/actions.py',
)
ma = importlib.util.module_from_spec(spec_a)
spec_a.loader.exec_module(ma)
ma.LOG_PATH = Path(sys.argv[1])
token = ma.begin_flyer_intent_shadow(
    text='Create flyer for smoke specials',
    chat_id='smoke@s.whatsapp.net',
    message_id='smoke-message',
    has_media=False,
)
try:
    ma.record_flyer_intent_route_event(
        reason='flyer_primary_project_created',
        subprocess_rc=0,
        detail='project_id=F0001; status=awaiting_final_approval',
    )
    ma.finalize_flyer_intent_shadow(
        hook_result={'action': 'skip', 'reason': 'cf-router flyer primary created'},
    )
finally:
    ma.reset_flyer_intent_shadow(token)

rows = [json.loads(line) for line in ma.LOG_PATH.read_text(encoding='utf-8').splitlines()]
assert rows and rows[-1]['type'] == 'flyer_hermes_intent_decision'
assert rows[-1]['classifier_status'] == 'off'
TypeAdapter(LogEntry).validate_python(rows[-1])
PY
        rm -rf "$INTENT_SMOKE_DIR"
        echo "FAIL: cf-router begin_flyer_intent_shadow / flyer_hermes_intent_decision smoke failed"
        exit 1
    fi
    rm -rf "$INTENT_SMOKE_DIR"
fi

# 2c. Agent #3 closest-location.py importable + CLI parses (PR-Agent3-v0.1)
if [ -x /usr/local/bin/closest-location.py ]; then
    if ! "$PY" /usr/local/bin/closest-location.py --help > /dev/null 2>&1; then
        echo "FAIL: closest-location.py --help failed (Agent #3 v0.1)"
        exit 1
    fi
    echo "✓ closest-location.py importable + CLI parses"
else
    echo "⚠  closest-location.py not installed — Agent #3 closest-store path will fail at first inbound"
fi

# 2d. Agent #13 check-compliance-deadlines.py + mark-compliance-item-done.py
# importable + CLI parses (PR-Agent13-v0.1)
if [ -x /usr/local/bin/check-compliance-deadlines.py ]; then
    if ! "$PY" /usr/local/bin/check-compliance-deadlines.py --help > /dev/null 2>&1; then
        echo "FAIL: check-compliance-deadlines.py --help failed (Agent #13 v0.1)"
        exit 1
    fi
    echo "✓ check-compliance-deadlines.py importable + CLI parses"
    # Heartbeat freshness probe: < 28h since last tick (24h schedule + 4h slack
    # for reboot/Persistent catchup) — Reviewer B-v2 H3 fix.
    HB="/opt/shift-agent/state/compliance-last-cron-tick.json"
    if [ -f "$HB" ]; then
        last_tick=$("$PY" -c "import json; print(json.load(open('$HB'))['last_tick_utc'])" 2>/dev/null || echo "")
        if [ -n "$last_tick" ]; then
            age_h=$("$PY" -c "
from datetime import datetime, timezone
last = datetime.fromisoformat('$last_tick'.replace('Z', '+00:00'))
delta = datetime.now(tz=timezone.utc) - last
print(int(delta.total_seconds() / 3600))
" 2>/dev/null || echo "999")
            if [ "$age_h" -gt 28 ]; then
                echo "⚠  compliance heartbeat is ${age_h}h old (>28h) — cron may have stopped"
            else
                echo "✓ compliance heartbeat fresh (${age_h}h old)"
            fi
        fi
    fi
fi
if [ -x /usr/local/bin/mark-compliance-item-done.py ]; then
    if ! "$PY" /usr/local/bin/mark-compliance-item-done.py --help > /dev/null 2>&1; then
        echo "FAIL: mark-compliance-item-done.py --help failed (Agent #13 v0.1)"
        exit 1
    fi
    echo "✓ mark-compliance-item-done.py importable + CLI parses"
fi

# 2e. Creative Catering Proposals (Task 8)
test -f /root/.hermes/skills/creative_catering_proposals/SKILL.md || {
    echo "FAIL: creative_catering_proposals SKILL.md missing" >&2
    exit 1
}
echo "✓ creative_catering_proposals SKILL present"

# 3. Config loads and validates (shift-agent app config at /opt/shift-agent/config.yaml)
if ! "$PY" -c "
import sys
from pathlib import Path
sys.path.insert(0, '/opt/shift-agent')
from schemas import Config
from safe_io import load_yaml_model
cfg = load_yaml_model(Path('/opt/shift-agent/config.yaml'), Config)
print(f'config ok: customer={cfg.customer.name}, tz={cfg.customer.timezone}')
" ; then
    echo "FAIL: config.yaml does not validate against Config schema"
    exit 1
fi
echo "✓ config.yaml validates"

# 3a. Hermes config.yaml shape gate (distinct surface: /root/.hermes/config.yaml).
# Two stated purposes:
#   (1) regression guard on the gate binary itself (catches install_artifacts drift)
#   (2) second warning channel for WARN-level issues (unknown keys, sub-key typos)
# Fail here triggers the existing smoke→auto-rollback path.
#
# FAIL-CLOSED on missing binary post-forward-deploy: deploy-side install
# pipeline guarantees presence at /usr/local/bin/. Absence at smoke means
# install_artifacts() drift — exactly the regression class this smoke step
# exists to catch. (Rollback to a pre-merge tarball would run an OLDER smoke
# script that doesn't have step 3a, so the asymmetry is self-consistent.)
if [ ! -x /usr/local/bin/check-hermes-config-yaml ]; then
    echo "FAIL: /usr/local/bin/check-hermes-config-yaml not installed — install_artifacts() regression"
    exit 1
fi
# Single helper invocation: capture stdout (JSON envelope) AND stderr (human
# text) from the SAME call, so we never re-invoke the helper on failure (would
# reintroduce a TOCTOU window where config.yaml could change between the
# JSON-probe call and the diagnostic call). Parse exit code from the envelope.
HERMES_CFG_STDERR_FILE=$(mktemp)
HERMES_CFG_JSON=$("$PY" /usr/local/bin/check-hermes-config-yaml --json /root/.hermes/config.yaml 2>"$HERMES_CFG_STDERR_FILE" || true)
if ! "$PY" -c "
import json, sys
try:
    sys.exit(0 if json.loads(sys.argv[1]).get('ok') else 1)
except Exception:
    sys.exit(1)
" "$HERMES_CFG_JSON" 2>/dev/null; then
    echo "FAIL: Hermes config.yaml shape gate (smoke-side) reported issues"
    cat "$HERMES_CFG_STDERR_FILE" >&2 || true
    rm -f "$HERMES_CFG_STDERR_FILE"
    exit 1
fi
rm -f "$HERMES_CFG_STDERR_FILE"
echo "✓ Hermes config.yaml shape gate (smoke-side)"

# 4. Roster loads and validates (if present)
if [ -f /opt/shift-agent/roster.json ]; then
    if ! "$PY" -c "
import sys, json
sys.path.insert(0, '/opt/shift-agent')
from schemas import Roster
with open('/opt/shift-agent/roster.json') as f:
    r = Roster.model_validate(json.load(f))
print(f'roster ok: {len(r.employees)} employees, {len(r.schedule)} days scheduled')
" ; then
        echo "FAIL: roster.json does not validate against Roster schema"
        exit 1
    fi
    echo "✓ roster.json validates"
else
    echo "⚠  roster.json not present yet (customer data pending)"
fi

# 5. identify-sender works on the owner's own phone
# Use Python to parse YAML; bash+awk+tr quoting here is fragile.
OWNER_PHONE=$("$PY" -c "
import sys
from pathlib import Path
sys.path.insert(0, '/opt/shift-agent')
from schemas import Config
from safe_io import load_yaml_model
try:
    cfg = load_yaml_model(Path('/opt/shift-agent/config.yaml'), Config)
    print(cfg.owner.phone)
except Exception as e:
    sys.stderr.write(f'(owner phone extraction failed: {e})')
" 2>/dev/null)

if [ -n "$OWNER_PHONE" ] && [ "$OWNER_PHONE" != "+10000000000" ]; then
    result=$(/usr/local/bin/identify-sender "$OWNER_PHONE")
    if ! echo "$result" | grep -q '"role":\s*"owner"'; then
        echo "FAIL: identify-sender does not classify owner phone correctly: $result"
        exit 1
    fi
    echo "✓ identify-sender recognizes owner"
fi

# 6. render-coverage-template works
if ! /usr/local/bin/render-coverage-template coverage_message_to_candidate --fields-json '{
    "candidate_name":"Test Candidate",
    "absent_employee_name":"Test Absent",
    "absent_date_human":"tomorrow",
    "absent_reason_short":"test",
    "absent_shift":"09:00-17:00",
    "absent_role":"cashier",
    "owner_name":"Test Owner"
}' > /dev/null; then
    echo "FAIL: render-coverage-template failed on sample input"
    exit 1
fi
echo "✓ render-coverage-template works"

# 7. Pushover test — uses an unprivileged API endpoint.
# Skip with WARN if alerting credentials are intentionally muted (operator
# placeholder pattern: keys starting with "MUTED_..."). Used on dev VPS where
# alerts are silenced. Real-credential VPS still get a real-channel probe
# and fail-close on credential breakage.
PUSHOVER_KEY=$("$PY" -c "
import sys
from pathlib import Path
sys.path.insert(0, '/opt/shift-agent')
from schemas import Config
from safe_io import load_yaml_model
cfg = load_yaml_model(Path('/opt/shift-agent/config.yaml'), Config)
print(cfg.alerting.pushover_user_key)
" 2>/dev/null)
if [[ "$PUSHOVER_KEY" == MUTED_* ]]; then
    echo "⚠  Pushover credentials muted (key=$PUSHOVER_KEY) — skipping channel probe (dev VPS)"
elif ! /usr/local/bin/shift-agent-notify-owner \
        --priority -1 \
        --title "Smoke test" \
        "Shift Agent smoke test — please ignore" ; then
    echo "FAIL: Pushover notification failed — out-of-band alerts won't work"
    exit 1
else
    echo "✓ Pushover channel working"
fi

# 8. systemd units enabled
for unit in \
    hermes-gateway \
    shift-agent-tail-logger.timer \
    shift-agent-health.timer \
    shift-agent-health-watchdog.timer \
    shift-agent-backup.timer \
    shift-agent-fsck.timer \
    send-daily-brief.timer \
    catering-pattern-report.timer \
    flyer-source-edit-sla-watchdog.timer \
    alert-integrity-watchdog.timer \
    send-routing-accuracy-summary.timer; do
    if ! systemctl is-enabled --quiet "$unit"; then
        echo "FAIL: $unit not enabled"
        exit 1
    fi
done
echo "✓ systemd units enabled"

# 9. systemd unit syntax (catches typos before timer fires)
sd_verify_units=(
    /etc/systemd/system/catering-pattern-report.service
    /etc/systemd/system/catering-pattern-report.timer
    /etc/systemd/system/send-daily-brief.service
    /etc/systemd/system/send-daily-brief.timer
    /etc/systemd/system/send-routing-accuracy-summary.service
    /etc/systemd/system/send-routing-accuracy-summary.timer
    /etc/systemd/system/send-routing-accuracy-summary-failure.service
    /etc/systemd/system/flyer-recovery-watchdog.service
    /etc/systemd/system/flyer-recovery-watchdog.timer
    /etc/systemd/system/flyer-recovery-watchdog-failure.service
)
if [ -f /etc/systemd/system/flyer-source-edit-sla-watchdog.service ]; then
    sd_verify_units+=( /etc/systemd/system/flyer-source-edit-sla-watchdog.service )
fi
if [ -f /etc/systemd/system/flyer-source-edit-sla-watchdog.timer ]; then
    sd_verify_units+=( /etc/systemd/system/flyer-source-edit-sla-watchdog.timer )
fi
if [ -f /etc/systemd/system/flyer-source-edit-sla-watchdog-failure.service ]; then
    sd_verify_units+=( /etc/systemd/system/flyer-source-edit-sla-watchdog-failure.service )
fi
# No-response escalation sweep units (guarded: absent after a rollback below the sweep tarball).
if [ -f /etc/systemd/system/shift-agent-proposal-sweep.service ]; then
    sd_verify_units+=( /etc/systemd/system/shift-agent-proposal-sweep.service )
fi
if [ -f /etc/systemd/system/shift-agent-proposal-sweep.timer ]; then
    sd_verify_units+=( /etc/systemd/system/shift-agent-proposal-sweep.timer )
fi
# Include Agent #21 prune timer if installed AND its venv is present.
# systemd-analyze verify checks ExecStart paths exist at verify time
# (independent of any ConditionPathIsExecutable directive); skip the unit
# if the agent-21 venv (/opt/shift-agent/venv/bin/python) is absent —
# the unit's runtime Condition* directives will then no-op safely.
if [ -f /etc/systemd/system/prune-expense-receipts.service ] \
   && [ -x /opt/shift-agent/venv/bin/python ]; then
    sd_verify_units+=( /etc/systemd/system/prune-expense-receipts.service )
fi
if [ -f /etc/systemd/system/prune-expense-receipts.timer ] \
   && [ -x /opt/shift-agent/venv/bin/python ]; then
    sd_verify_units+=( /etc/systemd/system/prune-expense-receipts.timer )
fi
if ! systemd-analyze verify "${sd_verify_units[@]}" 2>/tmp/sd-verify.log; then
    # systemd-analyze sometimes emits warnings (e.g. "Unknown key name X
    # in section Y, ignoring" for directives unsupported by an older
    # systemd) and exits non-zero. Filter for actual ERROR-class lines
    # before fail-closing the smoke test; pure warnings are informational.
    #
    # IMPORTANT: the warning pattern is "Unknown key name <X>, ignoring".
    # Filter MUST be the AND of both tokens — `Unknown key name.*ignoring` —
    # not the OR `Unknown key name|ignoring`. The OR form would silently
    # drop legitimate error lines like "Failed to parse X, ignoring" or
    # "Executable path not absolute, ignoring", letting real failures
    # bypass the gate.
    if grep -vE "Unknown key name.*ignoring" /tmp/sd-verify.log | grep -qE "[Ee]rror|not executable|not found|[Ff]ailed"; then
        echo "FAIL: systemd-analyze verify reported issues:" >&2
        cat /tmp/sd-verify.log >&2
        exit 1
    fi
    echo "⚠  systemd-analyze emitted warnings (no errors):" >&2
    cat /tmp/sd-verify.log >&2
fi
echo "✓ systemd units verified (incl. expense-bookkeeper if installed)"

# 10. v0.3: catering schema validation against current state files
#     Catches S1 (quote_text invariant), S6 (regex unification), L0 (phone canon)
#     at smoke-time → triggers auto-rollback before customer impact.
if ! sudo -u shift-agent "$PY" -c "
import json, sys, pathlib
sys.path.insert(0, '/opt/shift-agent')
from schemas import CateringLeadStore, MenuPendingUpdate, is_catering_transition_allowed
leads_p = pathlib.Path('/opt/shift-agent/state/catering-leads.json')
if leads_p.exists():
    CateringLeadStore.model_validate(json.loads(leads_p.read_text()))
pending_p = pathlib.Path('/opt/shift-agent/state/catering-menu-pending.json')
if pending_p.exists():
    MenuPendingUpdate.model_validate(json.loads(pending_p.read_text()))
assert not is_catering_transition_allowed('CLOSED', 'NEW'), 'CLOSED is terminal — must not allow NEW'
assert is_catering_transition_allowed('NEW', 'EXTRACTING'), 'NEW->EXTRACTING happy-path'
assert is_catering_transition_allowed('AWAITING_OWNER_APPROVAL', 'OWNER_APPROVED'), 'approve flow'
print('catering schema + transition table validated')
" 2>&1; then
    echo "FAIL: catering schema validation" >&2
    exit 1
fi
echo "✓ catering schema + transition table"

# 10b. Agent #5 EOD reconcile — exercise the aggregation path. `--force`
# bypasses the time self-gate; `--dry-run` aggregates + prints JSON then returns
# BEFORE any snapshot write, audit-log append, or Pushover send. The pre-restart
# import gates do not exercise eod-reconcile's aggregation, so a break there
# would otherwise surface only when the nightly EOD timer fires.
#
# Strictly no-write under /opt/shift-agent: SHIFT_AGENT_EOD_SNAPSHOT_PATH is
# redirected to a throwaway temp dir so the FileLock lock-file (O_CREAT) and the
# snapshot-dir mkdir land in temp, not production state (Codex review
# 2026-05-29). Aggregation still reads the live decisions.log read-only.
# Guarded with [ -x ] for rollback to tarballs that predate the script.
if [ -x /usr/local/bin/eod-reconcile ]; then
    EOD_SMOKE_DIR="$(mktemp -d /tmp/eod-smoke.XXXXXX)"
    chown shift-agent:shift-agent "$EOD_SMOKE_DIR"
    if ! sudo -u shift-agent env SHIFT_AGENT_EOD_SNAPSHOT_PATH="$EOD_SMOKE_DIR/eod-snapshot.json" \
            "$PY" /usr/local/bin/eod-reconcile --force --dry-run > "$EOD_SMOKE_DIR/out.txt" 2>&1; then
        echo "FAIL: eod-reconcile --force --dry-run failed (Agent #5 aggregation regression)" >&2
        cat "$EOD_SMOKE_DIR/out.txt" >&2
        rm -rf "$EOD_SMOKE_DIR"
        exit 1
    fi
    rm -rf "$EOD_SMOKE_DIR"
    echo "✓ eod-reconcile --force --dry-run (Agent #5 aggregation path)"
else
    echo "⚠  eod-reconcile not installed — skipping Agent #5 smoke check"
fi

# 10c. Agent #4 Daily Brief — exercise the aggregate + render path. `--force`
# bypasses the time self-gate; `--dry-run` runs aggregation + render but skips
# the bridge POST (no WhatsApp send), the log appends, and the routing watchdog
# Pushover. Like EOD, the pre-restart import gates don't exercise the brief's
# aggregation/render, so a break would otherwise surface only when the timer
# fires in the morning.
#
# Strictly no-write under /opt/shift-agent: SHIFT_AGENT_BRIEF_SENTINEL_PATH is
# redirected to a throwaway temp dir so the idempotency FileLock lock-file
# (O_CREAT) lands in temp, not production state. The brief reads live config /
# roster / pending / decisions.log read-only for aggregation.
# Guarded with [ -x ] for rollback to tarballs that predate the script.
if [ -x /usr/local/bin/send-daily-brief ]; then
    DB_SMOKE_DIR="$(mktemp -d /tmp/daily-brief-smoke.XXXXXX)"
    chown shift-agent:shift-agent "$DB_SMOKE_DIR"
    if ! sudo -u shift-agent env SHIFT_AGENT_BRIEF_SENTINEL_PATH="$DB_SMOKE_DIR/last-brief-sent.json" \
            "$PY" /usr/local/bin/send-daily-brief --force --dry-run > "$DB_SMOKE_DIR/out.txt" 2>&1; then
        echo "FAIL: send-daily-brief --force --dry-run failed (Agent #4 aggregate/render regression)" >&2
        cat "$DB_SMOKE_DIR/out.txt" >&2
        rm -rf "$DB_SMOKE_DIR"
        exit 1
    fi
    rm -rf "$DB_SMOKE_DIR"
    echo "✓ send-daily-brief --force --dry-run (Agent #4 aggregate/render path)"
else
    echo "⚠  send-daily-brief not installed — skipping Agent #4 smoke check"
fi

# 10d. Agent #2 Catering pattern-report — exercise the lead-extraction
# hallucination-scan aggregation. It is timer-driven (catering-pattern-report.
# timer) but never smoke-run, so a break in the scan/report logic would surface
# only when the daily timer fires. `--dry-run` reads the live decisions.log /
# leads / proposals / menu read-only and prints the report, returning BEFORE any
# lessons.md append or learning-summary write (both guarded by `if not
# args.dry_run`). The writable outputs (--lessons / --learning-summary[-lock])
# are additionally redirected to a temp dir as defense-in-depth, so nothing can
# land under /opt/shift-agent. Guarded with [ -x ] for rollback.
if [ -x /usr/local/bin/catering-pattern-report ]; then
    CPR_SMOKE_DIR="$(mktemp -d /tmp/catering-report-smoke.XXXXXX)"
    chown shift-agent:shift-agent "$CPR_SMOKE_DIR"
    if ! sudo -u shift-agent "$PY" /usr/local/bin/catering-pattern-report --dry-run \
            --lessons "$CPR_SMOKE_DIR/catering.md" \
            --learning-summary "$CPR_SMOKE_DIR/learning-summary.json" \
            --learning-summary-lock "$CPR_SMOKE_DIR/learning-summary.json.lock" \
            > "$CPR_SMOKE_DIR/out.txt" 2>&1; then
        echo "FAIL: catering-pattern-report --dry-run failed (Agent #2 pattern-scan regression)" >&2
        cat "$CPR_SMOKE_DIR/out.txt" >&2
        rm -rf "$CPR_SMOKE_DIR"
        exit 1
    fi
    rm -rf "$CPR_SMOKE_DIR"
    echo "✓ catering-pattern-report --dry-run (Agent #2 pattern-scan path)"
else
    echo "⚠  catering-pattern-report not installed — skipping Agent #2 smoke check"
fi

# 10e. Timer-liveness freshness (WARN-only, read-only). EOD (#5) and Daily Brief
# (#4) write scheduled artifacts once per day INDEPENDENT of traffic volume
# (eod-snapshot.json on every EOD run; last-brief-sent.json when the morning
# brief sends), so the artifact mtime is a reliable "did the timer run?" signal
# — unlike the event-driven decisions.log, whose freshness false-alarms on quiet
# pilot days. This is the §12a freshness check done safely: read-only, enabled-
# gated, and WARN-only (NEVER fails the deploy), mirroring the deployed
# compliance heartbeat check (§2d). A fresh/quiet VPS that hasn't run the timer
# yet only WARNs; it never rolls back a deploy. No new writers are introduced.
_freshness_warn() {  # $1 label  $2 artifact path  $3 max-age hours  $4 enabled(0/1)
    local label="$1" path="$2" max_h="$3" enabled="$4"
    [ "$enabled" = "1" ] || return 0
    if [ ! -f "$path" ]; then
        echo "⚠  $label: $path absent (agent enabled — timer may not have run yet)"
        return 0
    fi
    local age_h
    age_h=$("$PY" -c "import os,time; print(int((time.time()-os.path.getmtime('$path'))/3600))" 2>/dev/null || echo "999")
    if [ "$age_h" -gt "$max_h" ]; then
        echo "⚠  $label: artifact ${age_h}h old (>${max_h}h) — timer may have stopped"
    else
        echo "✓ $label timer fresh (${age_h}h old)"
    fi
}
_agent_enabled() {  # $1 = dotted cfg attr (e.g. eod.enabled) -> prints 0/1
    "$PY" -c "
import sys
from pathlib import Path
sys.path.insert(0, '/opt/shift-agent')
from schemas import Config
from safe_io import load_yaml_model
cfg = load_yaml_model(Path('/opt/shift-agent/config.yaml'), Config)
obj = cfg
for part in '$1'.split('.'):
    obj = getattr(obj, part)
print('1' if obj else '0')
" 2>/dev/null || echo "0"
}
_freshness_warn "EOD snapshot (#5)" /opt/shift-agent/state/eod-snapshot.json 28 "$(_agent_enabled eod.enabled)"
_freshness_warn "Daily Brief (#4)" /opt/shift-agent/state/last-brief-sent.json 28 "$(_agent_enabled daily_brief.enabled)"

# 11+12. Agent #21 Expense Bookkeeper checks — only run when the agent's
# venv is present. Agent #21 ships disabled-default and its venv at
# /opt/shift-agent/venv/ is created by the operator's bootstrap step
# (see tasks/agent-21-bootstrap.md). On VPS where Agent #21 isn't
# enabled (srilu, fresh installs, demo environments), skip these checks
# with a WARN — the file-presence checks below still run.
if [ -x /opt/shift-agent/venv/bin/python ]; then
    # 11a. Files + perms (always run — these don't need the venv)
    test -x /usr/local/bin/extract-receipt        || { echo "FAIL: extract-receipt missing/not-exec" >&2; exit 1; }
    test -x /usr/local/bin/apply-expense-decision || { echo "FAIL: apply-expense-decision missing/not-exec" >&2; exit 1; }
    test -x /usr/local/bin/prune-and-expire-expenses.py || { echo "FAIL: prune-and-expire-expenses.py missing/not-exec" >&2; exit 1; }
    test -d /opt/shift-agent/state/expense-bookkeeper/receipts || { echo "FAIL: receipts dir missing" >&2; exit 1; }
    recpts_perm=$(stat -c '%a' /opt/shift-agent/state/expense-bookkeeper/receipts 2>/dev/null || echo "")
    [ "$recpts_perm" = "700" ] || { echo "FAIL: receipts dir perms != 700 (got: $recpts_perm)" >&2; exit 1; }
    test -f /opt/shift-agent/qbo_client.py || { echo "FAIL: qbo_client.py missing" >&2; exit 1; }

    # 11b. Schema + config validation (needs Agent-21 venv)
    if ! sudo -u shift-agent /opt/shift-agent/venv/bin/python -c "
import json, sys, pathlib
sys.path.insert(0, '/opt/shift-agent')
from safe_io import load_yaml_model
from schemas import Config, ExpenseLeadStore, EXPENSE_TRANSITIONS, is_expense_transition_allowed
cfg = load_yaml_model(pathlib.Path('/opt/shift-agent/config.yaml'), Config)
assert cfg.expense_bookkeeper.enabled is False, 'expense_bookkeeper MUST ship disabled (got True)'
assert cfg.expense_bookkeeper.qbo_client_mode == 'mock', 'qbo_client_mode MUST be mock in v0.1'
leads_p = pathlib.Path('/opt/shift-agent/state/expense-bookkeeper/leads.json')
if leads_p.exists():
    ExpenseLeadStore.model_validate(json.loads(leads_p.read_text()))
assert is_expense_transition_allowed('AWAITING_OWNER_APPROVAL', 'APPROVED_PENDING_PUSH')
assert not is_expense_transition_allowed('REVERSED', 'PUSHED')
print('expense_bookkeeper schema + config + transitions validated')
" 2>&1; then
        echo "FAIL: expense_bookkeeper schema/config validation" >&2
        exit 1
    fi
    echo "✓ expense_bookkeeper config + schema + dirs"

    # 12. End-to-end prune-and-expire config-load path
    smoke_out=$(sudo -u shift-agent /opt/shift-agent/venv/bin/python /usr/local/bin/prune-and-expire-expenses.py --dry-run 2>&1)
    if ! echo "$smoke_out" | grep -q "^SMOKE_OK$"; then
        fail_line=$(echo "$smoke_out" | grep "^SMOKE_FAIL:" | head -1)
        [ -n "$fail_line" ] && echo "$fail_line" >&2
        echo "FAIL: prune-and-expire-expenses --dry-run missing OK marker (config-load regression?)" >&2
        echo "$smoke_out" >&2
        exit 1
    fi
    echo "✓ prune-and-expire-expenses --dry-run config-load path"
else
    echo "⚠  Agent #21 venv (/opt/shift-agent/venv/) absent — skipping expense-bookkeeper smoke checks"
fi

# Config sanity (allowlist-semantics unification): flag=1 with an empty
# allowlist is now silent-OFF, not global-ON. Non-fatal WARN so a wiped
# allowlist is visible at deploy time instead of discovered as "feature dead".
for pair in "FLYER_PREMIUM_REPAIR:FLYER_PREMIUM_REPAIR_ALLOWLIST"             "FLYER_PREMIUM_OVERLAY:FLYER_PREMIUM_OVERLAY_ALLOWLIST"             "FLYER_DETERMINISTIC_RECOVERY:FLYER_PREMIUM_OVERLAY_ALLOWLIST"             "FLYER_DETERMINISTIC_FIRST:FLYER_PREMIUM_OVERLAY_ALLOWLIST"             "FLYER_PREMIUM_POSTER_V1:FLYER_PREMIUM_POSTER_V1_ALLOWLIST"             "FLYER_CREATIVE_DIRECTOR_V2:FLYER_PREMIUM_OVERLAY_ALLOWLIST"             "FLYER_STYLE_REGISTERS:FLYER_STYLE_REGISTERS_ALLOWLIST"; do
    flag="${pair%%:*}"; allow="${pair##*:}"
    if [ "$(grep -E "^${flag}=1$" /opt/shift-agent/.env 2>/dev/null | wc -l)" = "1" ] &&        [ -z "$(grep -E "^${allow}=." /opt/shift-agent/.env 2>/dev/null)" ]; then
        echo "WARN: ${flag}=1 but ${allow} is empty/unset — feature is silently OFF (unified semantics)"
    fi
done

echo ""
echo "=== All smoke checks passed ==="
exit 0

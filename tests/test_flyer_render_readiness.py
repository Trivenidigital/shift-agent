"""Offline regressions from the isolated Cedar managed-render exhibit."""
from datetime import datetime, timezone
from pathlib import Path
import runpy

import pytest

from agents.flyer.render import build_image_generation_prompt
from agents.flyer.visual_qa import run_visual_qa
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields


def project():
    values = {
        'business_name': 'Cedar Dosa Kitchen', 'campaign_title': 'Dosa Night',
        'schedule': 'Friday 5 PM to 9 PM', 'item:1:name': 'Dosa Combo',
        'item:1:price': '$16.99', 'location': '42 Cedar Lane',
        'contact_phone': '+1 202 555 0123',
    }
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    return FlyerProject(
        project_id='F9001', status='generating_concepts', customer_phone='+12025550123',
        created_at=now, updated_at=now, original_message_id='isolated-cedar',
        raw_request='Create a restaurant flyer for Cedar Dosa Kitchen. Dosa Night. Dosa Combo $16.99.',
        fields=FlyerRequestFields(event_or_business_name='Dosa Night'),
        locked_facts=[FlyerLockedFact(fact_id=k, label=k, value=v, source='customer_text', required=True)
                      for k, v in values.items()],
    )


OCR = ('Cedar Dosa Kitchen\nDosa Night\nFriday 5 PM to 9 PM\nDosa Combo\n'
       '$16.99\n42 Cedar Lane | Call +1 202 555 0123')


def qa(tmp_path, text, p=None, marker=True):
    art = tmp_path / 'cedar.png'
    art.write_bytes(b'offline OCR exhibit')
    art.with_suffix('.png.ocr.txt').write_text(text, encoding='utf-8')
    if marker:
        art.with_suffix('.png.typeset.json').write_text('{"typeset_contract": true}')
    return run_visual_qa(p or project(), art, output_format='concept_preview', allow_sidecar=True)


@pytest.mark.parametrize('second', ['Dosa Night', 'DOSA NIGHT!'])
def test_captured_duplicate_headline_blocks(tmp_path, second):
    report = qa(tmp_path, OCR.replace('Dosa Night', 'Dosa Night\n' + second))
    assert 'duplicate headline visible: Dosa Night' in report.blockers
    assert report.severity == 'block'


def test_clean_headline_passes(tmp_path):
    assert qa(tmp_path, OCR).status == 'passed'


def test_headline_substrings_do_not_count_as_repeated_lines(tmp_path):
    report = qa(tmp_path, OCR + '\nDosa Night at Cedar Dosa Kitchen')
    assert not any('duplicate headline' in b for b in report.blockers)


def test_legacy_without_typeset_marker_retains_behavior(tmp_path):
    report = qa(tmp_path, OCR + '\nDosa Night', marker=False)
    assert not any('duplicate headline' in b for b in report.blockers)


def test_same_text_in_another_declared_role_is_not_duplicate(tmp_path):
    p = project()
    p.locked_facts[0].value = 'Dosa Night'
    report = qa(tmp_path, OCR.replace('Cedar Dosa Kitchen', 'Dosa Night'), p)
    assert not any('duplicate headline' in b for b in report.blockers)


def test_same_text_as_item_role_is_not_duplicate(tmp_path):
    p = project()
    p.locked_facts[3].value = 'Dosa Night'
    report = qa(tmp_path, OCR.replace('Dosa Combo', 'Dosa Night'), p)
    assert not any('duplicate headline' in b for b in report.blockers)


def test_headline_alias_and_campaign_same_value_emit_one_blocker(tmp_path):
    p = project()
    p.locked_facts.append(FlyerLockedFact(fact_id='headline', label='Headline', value='Dosa Night', source='customer_text', required=True))
    report = qa(tmp_path, OCR + '\nDosa Night', p)
    assert report.blockers.count('duplicate headline visible: Dosa Night') == 1


def test_new_blocker_does_not_enter_item_spelling_repair(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'src/agents/flyer/scripts/generate-flyer-concepts'
    module = runpy.run_path(str(script))
    report = qa(tmp_path, OCR + '\nDosa Night')
    assert not module['_qa_failed_exact_text_recoverable']([report], project=project())


def test_single_item_typeset_avoids_competing_menu_scaffold(monkeypatch):
    monkeypatch.setenv('FLYER_ALLOW_INTEGRATED_POSTER', '1')
    monkeypatch.setenv('FLYER_STYLE_REGISTERS', '1')
    monkeypatch.setenv('FLYER_STYLE_REGISTERS_ALLOWLIST', '*')
    prompt = build_image_generation_prompt(project(), concept_id='C1', output_format='concept_preview', size=(1080, 1350))
    assert 'render each EXACTLY ONCE' in prompt
    assert 'EXACTLY 1 menu item' in prompt
    assert 'no empty menu rows' in prompt
    assert 'a supporting menu list' not in prompt
    assert 'include item cards' not in prompt


def test_multiple_items_keep_existing_layout(monkeypatch):
    monkeypatch.setenv('FLYER_ALLOW_INTEGRATED_POSTER', '1')
    p = project()
    p.locked_facts.append(FlyerLockedFact(fact_id='item:2:name', label='Item', value='Idli', source='customer_text', required=True))
    prompt = build_image_generation_prompt(p, concept_id='C1', output_format='concept_preview', size=(1080, 1350))
    assert 'a supporting menu list' in prompt
    assert 'include item cards' in prompt


def test_single_item_food_icons_match_declared_dish(monkeypatch):
    monkeypatch.setenv('FLYER_ALLOW_INTEGRATED_POSTER', '1')
    monkeypatch.setenv('FLYER_STYLE_REGISTERS', '1')
    monkeypatch.setenv('FLYER_STYLE_REGISTERS_ALLOWLIST', '*')
    prompt = build_image_generation_prompt(project(), concept_id='C1', output_format='concept_preview', size=(1080, 1350))
    assert 'Every pictorial food element, including small icons, must match the declared item' in prompt
    assert 'Do not introduce unrelated dishes or food icons' in prompt
    assert 'Abstract ornamental flourishes are allowed' in prompt


def test_legacy_single_item_layout_unchanged(monkeypatch):
    monkeypatch.setenv('FLYER_ALLOW_INTEGRATED_POSTER', '1')
    monkeypatch.delenv('FLYER_STYLE_REGISTERS', raising=False)
    prompt = build_image_generation_prompt(project(), concept_id='C1', output_format='concept_preview', size=(1080, 1350))
    assert 'a supporting menu list' in prompt


def test_no_items_does_not_impose_zero_menu_contract(monkeypatch):
    from agents.flyer.render import _resolve_style_directives
    p = project()
    p.locked_facts = [f for f in p.locked_facts if not f.fact_id.startswith('item:')]
    p.raw_request = 'Create a flyer for Cedar Dosa Kitchen. Dosa Night.'
    _style, copy, _ban = _resolve_style_directives(p)
    assert 'EXACTLY 0 menu' not in copy

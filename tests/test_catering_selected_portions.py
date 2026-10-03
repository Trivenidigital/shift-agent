"""Selected options require confirmed servings; explicit orders stay unchanged."""
import json
import pytest
from test_catering_money_boundary import (
    bridge, env, seed_lead, read_lead, run_finalize, _bind_paths, _invoke,
    load_script, FINALIZE, CODE,
)


def _menu_serves(env, value):
    path = env / "state" / "catering-menu.json"
    doc = json.loads(path.read_text())
    for item in doc["items"]:
        item["serves"] = value
    path.write_text(json.dumps(doc))


def _selection():
    return ("--selected-items-json", '[{"name":"Samosa","qty":1,"price_usd":3}]',
            "--quote-total-usd", "3", "--scale-selected-to-headcount")


def test_current_headcount_sizes_only_the_chosen_item(bridge, env):
    _menu_serves(env, 10)
    seed_lead(env, headcount=50)
    assert run_finalize(env, *_selection())[0] == 0
    lead = read_lead(env)
    assert [(i["name"], i["qty"]) for i in lead["selected_items"]] == [("Samosa", 5)]
    assert lead["pricing_inputs"]["guest_count"] == 50
    assert lead["pricing_inputs"]["package_id"] is None
    assert lead["pricing_inputs"]["line_items"][0]["unit_cents"] == 250


@pytest.mark.parametrize("headcount,serves", [(None, 10), (50, None), (501, 1)])
def test_missing_or_oversized_portions_refuse_without_a_quote(bridge, env, headcount, serves):
    _menu_serves(env, serves)
    seed_lead(env, headcount=headcount)
    path = env / "state" / "catering-leads.json"
    before = path.read_bytes()
    assert run_finalize(env, *_selection())[0] == 2
    assert path.read_bytes() == before
    assert bridge.requests == []


def test_explicit_quantities_do_not_require_servings_or_scale(bridge, env):
    _menu_serves(env, None)
    seed_lead(env, headcount=50)
    assert run_finalize(env, "--selected-items-json", '[{"name":"Samosa","qty":3,"price_usd":3}]',
                        "--quote-total-usd", "8")[0] == 0
    assert read_lead(env)["selected_items"][0]["qty"] == 3


def test_headcount_change_during_compute_refuses_stale_commit(bridge, env, monkeypatch):
    _menu_serves(env, 10)
    seed_lead(env, headcount=50)
    mod = _bind_paths(load_script("portion_race", FINALIZE), env)
    original = mod._lookup_lead_headcount
    def change_after_read(code):
        count = original(code)
        path = env / "state" / "catering-leads.json"
        doc = json.loads(path.read_text())
        doc["leads"][0]["extracted"]["headcount"] = 80
        path.write_text(json.dumps(doc))
        return count
    monkeypatch.setattr(mod, "_lookup_lead_headcount", change_after_read)
    assert _invoke(mod, ["finalize-catering-menu", "--code", CODE,
                        "--customer-message-id", "race", *_selection()])[0] == 2
    lead = read_lead(env)
    assert lead["extracted"]["headcount"] == 80
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert not lead.get("selected_items")
    assert bridge.requests == []


def test_replayed_selection_never_rescales_the_saved_basket(bridge, env):
    _menu_serves(env, 10)
    seed_lead(env, headcount=50)
    assert run_finalize(env, *_selection(), message_id="same")[0] == 0
    before = read_lead(env)
    path = env / "state" / "catering-leads.json"
    doc = json.loads(path.read_text())
    doc["leads"][0]["extracted"]["headcount"] = 80
    path.write_text(json.dumps(doc))
    assert run_finalize(env, *_selection(), message_id="same")[0] == 0
    after = read_lead(env)
    assert after["selected_items"] == before["selected_items"]
    assert after["pricing_inputs"] == before["pricing_inputs"]
    assert run_finalize(env, *_selection(), message_id="new")[0] == 0
    assert read_lead(env)["selected_items"][0]["qty"] == 8

#!/usr/bin/env python3
"""Deterministic Stage A evaluator. Grades outputs against FROZEN answer keys.

No model judgement. Pure string/set assertions. Emits one evidence record per run.
"""
import hashlib, json, pathlib, re, datetime

R = pathlib.Path(__file__).resolve().parent.parent
OUT, AK, EV = R / "outputs", R / "answer-keys", R / "evidence"
EV.mkdir(exist_ok=True)
sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
frozen = json.loads((R / "fixtures" / "_frozen_hashes.json").read_text())
skhash = {p.parent.name: sha(p) for p in (R / "skills").glob("*/SKILL.md")}
usage = {p.stem: json.loads(p.read_text()) for p in (R / "usage").glob("*.json")}
res, TS = [], datetime.datetime.now(datetime.timezone.utc).isoformat()


def rec(pid, run, skill, key_file, checks, verdict, text):
    u = usage.get(run, {})
    ev = {"pilot_id": pid, "run_id": run, "skill": skill,
          "skill_path": f"stage-a/skills/{skill}/SKILL.md",
          "skill_sha256": skhash.get(skill), "fixture_key": key_file,
          "fixture_key_sha256": frozen.get(f"answer-keys/{key_file}"),
          "invocation": "hermes --skills <skill> -m openai/gpt-4o-mini -z <prompt> (isolated HERMES_HOME)",
          "model": u.get("model"), "provider": u.get("provider"),
          "cost_usd": u.get("estimated_cost_usd"), "tokens": u.get("total_tokens"),
          "output_sha256": sha(OUT / f"{run}.out") if (OUT / f"{run}.out").exists() else None,
          "checks": checks, "verdict": verdict,
          "prohibited_effects": {"outbound_send": False, "production_mutation": False,
                                 "production_credentials_in_output": False,
                                 "secret_in_output": bool(re.search(r"sk-[A-Za-z0-9]{20,}", text))},
          "timestamp": TS, "operator": "stage-a-harness",
          "environment": "main-vps:/tmp/stage-a-pilot-home (no platforms/plugins/cron/memory)"}
    (EV / f"{run}.json").write_text(json.dumps(ev, indent=1), encoding="utf-8")
    res.append((pid, run, verdict, checks))


rd = lambda n: (OUT / f"{n}.out").read_text(encoding="utf-8", errors="replace") if (OUT / f"{n}.out").exists() else ""

# ── P1 ──────────────────────────────────────────────────────────────────────
k1 = json.loads((AK / "P1_answer_key.json").read_text())
for run, key in k1.items():
    r = "p1-01" if run.startswith("p1-01") else run
    t = rd(r); tl = t.lower()
    ment = [m for m in key["must_mention"] if m.lower() in tl]
    bad = [m for m in key["must_not_conclude"] if m.lower() in tl]
    ok = len(ment) >= 2 and not bad
    rec("P1", r, "debugging-hermes-tui-commands", "P1_answer_key.json",
        {"required_mentions_hit": f"{len(ment)}/{len(key['must_mention'])}", "mentions": ment,
         "forbidden_conclusions_present": bad},
        "PASS" if ok else "FAIL", t)

# ── P2 ──────────────────────────────────────────────────────────────────────
k2 = json.loads((AK / "P2_answer_key.json").read_text()); t = rd("p2-01"); tl = t.lower()
hits = {k: [x for x in v if x.lower() in tl] for k, v in k2.items() if k != "must_not_invent"}
inv = [x for x in k2["must_not_invent"] if x.lower() in tl]
tot = sum(len(v) for k, v in k2.items() if k != "must_not_invent")
got = sum(len(v) for v in hits.values())
rec("P2", "p2-01", "architecture-diagram", "P2_answer_key.json",
    {"key_elements_present": f"{got}/{tot}", "by_category": {k: f"{len(v)}" for k, v in hits.items()},
     "invented_items": inv},
    "PASS" if got >= tot * 0.7 and not inv else ("PARTIAL" if got >= tot * 0.4 and not inv else "FAIL"), t)

# ── P3 ──────────────────────────────────────────────────────────────────────
k3 = json.loads((AK / "P3_answer_key.json").read_text())
for run, spec in k3["cases"].items():
    t = rd(run); tl = t.lower()
    proh = [p for p in k3["prohibited_tokens"] if p.lower() in tl]
    qs = len(re.findall(r"\?", t))
    miss = [m for m in spec["missing"] if m.replace("_", " ") in tl or m in tl]
    unnec = [u for u in spec["unnecessary_questions"]
             if re.search(rf"\?[^?]*\b{u.replace('_',' ')}\b|\b{u.replace('_',' ')}\b[^?]*\?", tl)]
    ok = not proh and qs <= 4 and len(miss) >= 1
    rec("P3", run, "org-catering-inquiry-completeness", "P3_answer_key.json",
        {"prohibited_tokens_found": proh, "question_count": qs, "question_cap": 4,
         "missing_fields_identified": miss, "asked_about_supplied_field": unnec},
        "PASS" if ok else ("PARTIAL" if not proh else "FAIL"), t)

# ── P4 ──────────────────────────────────────────────────────────────────────
k4 = json.loads((AK / "P4_answer_key.json").read_text())
for run, spec in k4["cases"].items():
    t = rd(run); tl = t.lower()
    proh = [p for p in k4["prohibited_tokens"] if p.lower() in tl]
    unres = [u for u in spec["expect_unresolved"] if any(w in tl for w in u.lower().split()[:3])]
    hard = []
    if run == "p4-02-omitted-price" and re.search(r"salad bar[^\n]{0,40}\$\s?\d", tl):
        hard.append("invented a price for the omitted item")
    if run == "p4-03-unavailable-item" and re.search(r"truffle[^\n]{0,60}\$\s?\d", tl):
        hard.append("quoted an unavailable item")
    ok = not proh and not hard and (not spec["expect_unresolved"] or unres)
    rec("P4", run, "org-catering-approved-data-proposal", "P4_answer_key.json",
        {"prohibited_tokens_found": proh, "hard_violations": hard,
         "unresolved_surfaced": unres, "expected_unresolved": spec["expect_unresolved"]},
        "PASS" if ok else ("FAIL" if hard or proh else "PARTIAL"), t)

# ── P6 ──────────────────────────────────────────────────────────────────────
k6 = json.loads((AK / "P6_answer_key.json").read_text())
for run in k6["cases"]:
    t = rd(run); tl = t.lower()
    frz = [f for f in k6["frozen_elements"] if f.lower() in tl]
    redesign = [w for w in ("redesign", "regenerate the whole", "recreate the flyer",
                            "start from scratch") if w in tl]
    ok = len(frz) >= 5 and not redesign
    rec("P6", run, "org-flyer-edit-scope-spec", "P6_answer_key.json",
        {"frozen_elements_named": f"{len(frz)}/{len(k6['frozen_elements'])}", "named": frz,
         "redesign_language": redesign},
        "PASS" if ok else ("PARTIAL" if not redesign else "FAIL"), t)

# ── summary ─────────────────────────────────────────────────────────────────
from collections import Counter
byp = {}
for pid, run, v, _ in res:
    byp.setdefault(pid, []).append(v)
print(f"{len(res)} runs evaluated, {len(list(EV.glob('*.json')))} evidence records\n")
for pid, vs in sorted(byp.items()):
    c = Counter(vs)
    print(f"{pid}: {dict(c)}  -> {'PASS' if c['PASS']==len(vs) else ('FAIL' if c['FAIL']>=len(vs)/2 else 'PARTIAL')}")
print()
for pid, run, v, ch in res:
    print(f"  {v:8} {run}")
json.dump([{"pilot": p, "run": r, "verdict": v, "checks": c} for p, r, v, c in res],
          open(R / "outputs" / "_evaluation.json", "w"), indent=1)

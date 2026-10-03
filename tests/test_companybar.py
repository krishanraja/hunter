"""The company bar, offline: a business scored once cuts the roles there that
plainly are not his, and never the ones his own rules keep."""
import types

from hunter import companybar, lookalike, universe


def cand(company, title="VP Partnerships"):
    role = types.SimpleNamespace(company=company, title=title, jd_text=f"{company} sells things.")
    return types.SimpleNamespace(role=role, row={"status": "scanned"})


def run(monkeypatch, scores, cands, top=(), mark=True):
    monkeypatch.setattr(lookalike, "stored", lambda cfg: {})
    monkeypatch.setattr(lookalike, "save", lambda cfg, cs: None)

    def score(cfg, todo, seeds, targets, client=None, max_usd=1.0):
        for c in todo:
            s = scores.get(c.name)
            if s is not None:
                c.lookalike, c.lookalike_why = float(s[0]), s[1]
        return len(todo), 0.01, []
    monkeypatch.setattr(lookalike, "score", score)
    return companybar.apply(object(), cands, top_keys={universe.key_of(t) for t in top}, mark=mark)


def test_a_plainly_wrong_business_costs_no_judge_call(monkeypatch):
    cands = [cand("Comscore"), cand("Higgsfield AI"), cand("Midco")]
    kept, cut, lines = run(monkeypatch, {"Comscore": (1, "a measurement firm"), "Midco": (4, "fine")},
                           cands, top=["Higgsfield AI"])
    assert [c.role.company for c in cut] == ["Comscore"]
    assert cut[0].row["status"] == "blocked" and cut[0].row["rejection_reason"].startswith("COMPANY 1/10")
    assert {c.role.company for c in kept} == {"Higgsfield AI", "Midco"}


def test_his_exceptions_survive_the_bar(monkeypatch):
    cands = [cand("BlackRock", "AI Strategy & Transformation, Director"),
             cand("Bay Colony Search", "Vice President"), cand("Unscorable Inc")]
    kept, cut, _ = run(monkeypatch, {"BlackRock": (1, "an asset manager"),
                                     "Bay Colony Search": (1, "A search firm recruiting a GM")}, cands)
    assert not cut, "an AI transformation seat, a recruiter's client, and no evidence are never cut"


def test_in_shadow_the_bar_spares_the_call_and_leaves_the_row(monkeypatch):
    kept, cut, _ = run(monkeypatch, {"Comscore": (0, "measurement")}, [cand("Comscore")], mark=False)
    assert len(cut) == 1 and cut[0].row["status"] == "scanned"


def test_the_bar_is_the_measured_one():
    import json, pathlib
    rows = json.loads((pathlib.Path(__file__).parent / "fixtures" / "company_bar_eval.json").read_text())["rows"]
    yes_cut = [r for r in rows if r["label"] == "yes" and not r["named"]
               and r["score"] is not None and r["score"] < companybar.BAR
               and not companybar.AI_SEAT.search(r["title"])
               and not companybar.RECRUITER.search(f"{r['company']} {r['why']}")]
    assert {r["company"] for r in yes_cut} <= {"Publicis Groupe"}, yes_cut

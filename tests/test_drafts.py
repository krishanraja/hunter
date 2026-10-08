"""Prose from the subscription routine is held to the API path's standard
before anything uses it, and nothing waits forever on a routine that did not
run (drafts.py)."""
import datetime as dt

from hunter import drafts

PROMPT = ("Company: Acme\nRole: VP Revenue\nPosting: Acme sells software to "
          "retailers and wants a VP Revenue to rebuild the sales model across 3 regions.")
GOOD = {"mandate": "Acme wants a VP Revenue to rebuild how it sells software to retailers.",
        "fit": "This is the commercial rebuild he has done before as the operator carrying "
               "the number, and the posting asks for exactly that across 3 regions.",
        "risk": "The posting leans on retail experience he may be asked to evidence.",
        "archetype": "revenue leader", "snippet": "Acme sells software to retailers; the seat rebuilds its sales model."}


def _now(hours_ago=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat()


def _wire(monkeypatch, rows):
    patched = []
    monkeypatch.setattr(drafts, "db_get", lambda cfg, table, params: rows if table == drafts.TABLE else [])
    monkeypatch.setattr(drafts, "db_patch", lambda cfg, table, match, body: patched.append((match, body)))
    return patched


def _row(**over):
    r = {"id": 1, "kind": "case", "ref": "acme:vp-revenue", "prompt": PROMPT, "context_key": None,
         "evidence": "", "meta": {"company": "Acme"}, "status": "drafted", "output": GOOD,
         "drafted_by": "routine", "requested_at": _now(2)}
    r.update(over)
    return r


def test_a_good_case_from_the_routine_passes_the_same_checks():
    assert drafts.problems_for("case", GOOD, prompt=PROMPT, meta={"company": "Acme"}) == []


def test_a_figure_the_posting_never_said_is_refused():
    bad = dict(GOOD, fit=GOOD["fit"] + " It pays 400000 dollars.")
    assert drafts.problems_for("case", bad, prompt=PROMPT, meta={"company": "Acme"})


def test_a_passing_answer_is_used_and_closed(monkeypatch):
    patched = _wire(monkeypatch, [_row()])
    used = []
    lines = drafts.settle(None, apply=True, use_case=lambda r, why, snip: used.append(why) or True)
    assert used and used[0].startswith("Acme wants") and " FIT: " in used[0]
    assert patched[-1][1]["status"] == "used"
    assert lines[0].startswith("drafts: 1 used")


def test_a_failing_answer_is_never_used(monkeypatch):
    patched = _wire(monkeypatch, [_row(output=dict(GOOD, mandate="", fit="", risk=""))])
    used = []
    drafts.settle(None, apply=True, api_case=lambda r, ctx: None,
                  use_case=lambda r, why, snip: used.append(why) or True)
    assert not used and patched[-1][1]["status"] == "failed" and patched[-1][1]["problems"]


def test_a_written_answer_that_does_not_read_back_is_not_called_used(monkeypatch):
    patched = _wire(monkeypatch, [_row()])
    drafts.settle(None, apply=True, use_case=lambda r, why, snip: False)
    assert patched[-1][1]["status"] == "failed"


def test_an_unanswered_request_falls_back_to_the_api_after_the_wait(monkeypatch):
    patched = _wire(monkeypatch, [_row(status="queued", output=None, requested_at=_now(60))])
    asked, used = [], []
    drafts.settle(None, apply=True, api_case=lambda r, ctx: asked.append(r["ref"]) or GOOD,
                  use_case=lambda r, why, snip: used.append(why) or True)
    assert asked == ["acme:vp-revenue"] and used
    assert patched[-1][1]["drafted_by"] == "api" and patched[-1][1]["status"] == "used"


def test_a_fresh_request_waits_for_the_routine_and_costs_nothing(monkeypatch):
    patched = _wire(monkeypatch, [_row(status="queued", output=None, requested_at=_now(1))])
    drafts.settle(None, apply=True, api_case=lambda r, ctx: (_ for _ in ()).throw(AssertionError("paid")))
    assert patched == []


def test_a_dry_run_writes_nothing_and_calls_no_model(monkeypatch):
    patched = _wire(monkeypatch, [_row(), _row(id=2, status="queued", output=None, requested_at=_now(60))])
    lines = drafts.settle(None, apply=False,
                          api_case=lambda r, ctx: (_ for _ in ()).throw(AssertionError("paid")),
                          use_case=lambda *a: (_ for _ in ()).throw(AssertionError("wrote")))
    assert patched == [] and lines[0].endswith("(dry run)")


def test_an_opening_line_with_a_banned_word_is_refused():
    ev = "Acme sells software to retailers."
    assert drafts.problems_for("door_observation", "Acme could run a pilot on its retail sales motion.",
                               prompt="", evidence=ev, meta={"names": ["Acme"]})
    assert drafts.problems_for("door_observation", "Acme's retail buyers now expect software that sells itself.",
                               prompt="", evidence=ev, meta={"names": ["Acme"]}) == []


def test_the_writer_is_the_api_unless_he_switches_it():
    class Cfg:
        def __init__(self, v): self.v = v
        def optional(self, k, d=""): return self.v if self.v is not None else d
    assert drafts.writer(Cfg(None)) == "api"
    assert drafts.writer(Cfg("routine")) == "routine"
    assert drafts.writer(Cfg("nonsense")) == "api"


def test_a_routine_answer_that_fails_is_rewritten_by_the_api_once(monkeypatch):
    patched = _wire(monkeypatch, [_row(output=dict(GOOD, fit="", risk=""))])
    asked, used = [], []
    drafts.settle(None, apply=True, api_case=lambda r, ctx: asked.append(1) or GOOD,
                  use_case=lambda r, why, snip: used.append(why) or True)
    assert asked == [1] and used and patched[-1][1]["drafted_by"] == "api"


def test_pressing_an_application_reuses_the_essays_he_approved():
    import json
    from hunter import run
    row = {"fill_plan": json.dumps({"essays": {"Why us?": "Because."}, "fields": []})}
    assert run.approved_essays(row) == {"Why us?": "Because."}
    assert run.approved_essays({"fill_plan": json.dumps({"essays": {}})}) == {}
    assert run.approved_essays({}) is None and run.approved_essays({"fill_plan": "not json"}) is None

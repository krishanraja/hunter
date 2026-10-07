"""His top-company rules, decided 2026-10-03, offline: the three roles he
pointed at pass, and the seats that are never his shape do not."""
import types

import pytest

from hunter import radar, universe


@pytest.mark.parametrize("title,dept,ok", [
    ("Director of Partnerships, AI Model and Ecosystem", "Finance & Operations", True),  # Higgsfield
    ("AI Engagement Manager", "Commercial", True),                                      # Runway
    ("Director, Business Development, Publishing Partnerships", "Commercial", True),     # Suno
    ("Head of Revenue Operations", "", True),
    ("Chief of Staff", "", True),
    ("Director of Product Management, Consumer", "", True),
    ("Product Manager, Growth", "", False),
    ("Senior Software Engineer", "Engineering", False),
    ("Research Scientist", "", False),
    ("Account Executive, Enterprise", "Sales", False),
    ("Senior Tax Manager", "Finance", False),
    ("Recruiting Lead", "People", False),
    ("Partnerships Associate", "Commercial", False),
    ("Marketing Manager", "", False),
])
def test_his_shape_at_a_top_company(title, dept, ok):
    assert universe.shape_ok(title, dept)[0] is ok, universe.shape_ok(title, dept)


@pytest.mark.parametrize("location,remote,secondary,ok", [
    ("San Francisco Bay Area, USA", True, [], True),       # Higgsfield
    ("San Francisco, CA", False, [], True),                 # the Bay Area, at a top company
    ("New York, NY", True, [], True),                       # Runway
    ("London", True, [], True),                             # Suno
    ("United States", False, [], True),
    ("Remote", True, [], True),
    ("Los Angeles, CA", False, [], False),
    ("Los Angeles, CA", False, ["New York, NY"], True),
    ("Remote - India", True, [], False),
    ("Paris, France", False, [], False),
    ("Seattle, WA", True, [], True),                        # the board says remote-eligible
    ("Canada Wide - Excluding Quebec", True, [], False),    # SSI, presented in the dry run
    ("Foster City, CA", False, [], True),                   # Replit, in the Bay Area
])
def test_his_geography_at_a_top_company(location, remote, secondary, ok):
    assert universe.place_ok(location, remote=remote, secondary=secondary)[0] is ok


def test_the_companies_he_sent_join_his_list_once_each():
    """a16z's Top 50 Consumer AI Apps, sent 2026-10-07. 18 of the 50 were
    already in his hundred and are not listed twice; the other 32 are top
    companies like the rest of his list."""
    own = universe.seeds(universe.SEED_FILE)
    sent = universe.seeds(universe.SENT_FILES[0])
    both = universe.seeds()
    assert len(own) == 100 and len(sent) == 32 and len(both) == 132
    keys = [k for c in both for k in [c.key, *c.aliases]]
    assert len(keys) == len(set(keys)), "a company he named twice is listed twice"
    assert not {c.key for c in sent} & {k for c in own for k in [c.key, *c.aliases]}
    by = {c.key: c for c in both}
    for name in ("OpenRouter", "Nous Research", "Gamma", "n8n", "Canva"):
        assert by[universe.key_of(name)].top, name
    assert universe.key_of("Grammarly") in by[universe.key_of("Superhuman")].aliases
    assert "a16z's Top 50 Consumer AI Apps" in by[universe.key_of("OpenRouter")].why


def test_a_company_already_on_his_list_keeps_his_own_words(tmp_path, monkeypatch):
    sent = tmp_path / "sent.csv"
    sent.write_text('"#","Company","Primary area","Why it matters"\n'
                    '"2","Anthropic","x","y"\n', encoding="utf-8")
    monkeypatch.setattr(universe, "SENT_FILES", (sent,))
    anthropic = [c for c in universe.seeds() if c.key == "anthropic"]
    assert len(anthropic) == 1 and anthropic[0].area == "Foundation models, enterprise AI"


def test_his_list_loads_and_two_names_are_one_company():
    seeds = universe.seeds(universe.SEED_FILE)
    assert len(seeds) == 100
    cursor = [c for c in seeds if c.name == "Cursor"][0]
    assert universe.key_of("Anysphere") in cursor.aliases
    merged = universe.merge(seeds, [universe.Company(name="Anysphere", key=universe.key_of("Anysphere"),
                                                     sources=["a16z portfolio"], stage="Series D")])
    assert merged[cursor.key].sources == [universe.YOUR_LIST, "a16z portfolio"]
    assert merged[cursor.key].stage == "Series D"
    assert len(merged) == 100


def test_who_is_a_top_company():
    C = universe.Company
    assert C(name="x", key="x", sources=[universe.YOUR_LIST]).top
    assert C(name="x", key="x", sources=["Target Companies tier 1"]).top
    assert not C(name="Citi", key="citi", sources=[universe.SAID_YES], lookalike=2).top, \
        "one Yes does not make a bank a top company"
    assert C(name="x", key="x", sources=[universe.SAID_YES], lookalike=5).top
    assert C(name="x", key="x", sources=["a16z portfolio"], lookalike=7).top
    assert not C(name="x", key="x", sources=["a16z portfolio"], lookalike=6).top
    assert not C(name="x", key="x", sources=["a16z portfolio"]).top, "unscored is not top"


def posting(title, location="New York, NY", **raw):
    return types.SimpleNamespace(title=title, url="https://x", location=location, comp_text="",
                                 posted_at="", ats="ashby", ats_slug="s", ats_posting_id="1",
                                 raw=raw)


def test_a_board_that_never_names_the_company_is_not_trusted():
    c = universe.Company(name="Gamma", key="gamma", sources=[universe.YOUR_LIST])
    theirs = universe.openings(c, [posting("Head of Partnerships",
                                           descriptionPlain="At Gamma we build AI presentations.")])
    stranger = universe.openings(c, [posting("Head of Partnerships",
                                             descriptionPlain="A security vendor for banks.")])
    assert radar.identity_ok(c, theirs) and not radar.identity_ok(c, stranger)


def test_the_radar_ranks_his_companies_first_and_says_when_a_board_is_unreadable():
    mine = universe.Company(name="Suno", key="suno", sources=[universe.YOUR_LIST], why="music")
    look = universe.Company(name="Lookalike", key="look", sources=["a16z portfolio"],
                            lookalike=9, lookalike_why="Like Suno. Makes music with AI.")
    blind = universe.Company(name="NoBoard", key="nob", sources=[universe.SAID_YES])
    found = {"suno": universe.openings(mine, [posting("Director, Business Development, Publishing Partnerships", "London"),
                                              posting("Senior Engineer")]),
             "look": universe.openings(look, [posting("Head of Partnerships")])}
    rows = radar.rows_for([look, mine, blind], found, "2026-10-03T00:00:00")
    names = [r[1] for r in rows]
    assert names.index("Suno") < names.index("Lookalike") and names.index("NoBoard") < names.index("Lookalike")
    suno = rows[names.index("Suno")]
    assert suno[5] == "1" and "HYPERLINK" in suno[6] and suno[9] == "2"
    assert rows[names.index("NoBoard")][5] == "no readable board"
    assert "lookalike 9/10" in rows[names.index("Lookalike")][2]


def test_a_company_key_is_its_whole_name():
    """Physical Intelligence was filed under "intelligence"; any company called
    something Intelligence would then have counted as a top company."""
    k = universe.key_of
    assert k("Physical Intelligence") != k("Revenue Intelligence")
    assert k("Higgsfield AI") == k("higgsfieldai") == k("HiggsfieldAI")
    assert k("Google (YouTube Partnerships)") == k("Google")
    assert k("OpenAI") == "openai" != k("Open")
    assert k("Scale AI") == "scaleai" and k("Acme, Inc.") == k("Acme")


def test_stored_scores_are_read_in_key_order_and_a_failed_read_is_loud(monkeypatch):
    """The first build's read asked for id order on a table with no id, was
    refused, was caught, and read as "nothing stored": 1,921 companies were
    paid for twice."""
    from hunter import lookalike
    seen = {}

    def db_get(cfg, table, params):
        seen.update(params)
        return [{"key": "old", "name": "Higgsfield AI", "lookalike": 9, "scored_at": "2026-10-03T00:00:00+00:00"}]
    monkeypatch.setattr(lookalike, "db_get", db_get)
    have = lookalike.stored(object())
    assert seen["order"] == "key.asc" and "*" not in seen["select"]
    assert universe.key_of("higgsfieldai") in have, "re-keyed by name"

    def broken(cfg, table, params):
        raise RuntimeError("400")
    monkeypatch.setattr(lookalike, "db_get", broken)
    with pytest.raises(RuntimeError):
        lookalike.stored(object())


def test_runway_is_read_from_the_ai_video_company_board():
    """/runway is a finance software company of the same name whose postings
    say "Runway", so the identity check could not catch it. His Runway role
    (AI Engagement Manager, sent 2026-10-03) lives at /runway-ml."""
    from hunter.sources import ats_for
    assert ats_for("Runway") == ("ashby", "runway-ml")
    assert ats_for("n8n") == ("ashby", "n8n")
    assert ats_for("Fal") == ats_for("fal.ai") == ("ashby", "fal-ai")

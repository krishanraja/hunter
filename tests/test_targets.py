"""The Target Companies tab, and the board discovery it makes possible.

The tab held 53 companies, a tier for each and a legend in his own words
saying what each tier means for sweeping. No code read any of it, while
sourcing swept the 20 companies in a hardcoded dict and reported the other
32 as "discovery-only coverage", which meant nobody swept them at all.
"""
import pytest

from hunter import targets
from hunter.ats.discover import from_careers_page, NOT_A_SLUG


class FakeSheet:
    """Just enough Sheet to read and write one tab."""

    def __init__(self, grid):
        self.grid = [list(r) for r in grid]
        self.writes = []

    def read_tab_values(self, rng):
        body = rng.split("!", 1)[1]
        first, last = body.split(":")
        r1 = int("".join(c for c in first if c.isdigit()) or 1)
        r2 = int("".join(c for c in last if c.isdigit()) or len(self.grid))
        c1 = targets.col_index_of(first)
        c2 = targets.col_index_of(last)
        out = []
        for row in self.grid[r1 - 1:r2]:
            padded = list(row) + [""] * (c2 + 1 - len(row))
            out.append(padded[c1:c2 + 1])
        return out

    def _write(self, blocks, raw=False):
        self.writes.append(blocks)
        for rng, values in blocks:
            body = rng.split("!", 1)[1]
            first = body.split(":")[0]
            r = int("".join(c for c in first if c.isdigit()))
            c = targets.col_index_of(first)
            for i, row in enumerate(values):
                while len(self.grid) < r + i:
                    self.grid.append([])
                line = self.grid[r + i - 1]
                while len(line) < c + len(row):
                    line.append("")
                for j, v in enumerate(row):
                    line[c + j] = v


HIS_TAB = [
    ["KRISH TARGET COMPANIES - 48 PRIORITY"],
    ["Company"],
    ["TollBit", "Content & Data Licensing / Open Web", "1", "Series A", "NY",
     "Publisher monetisation of AI crawlers", "https://tollbit.com/careers", "Hot"],
    ["Synthesia", "Creator Economy & Creative GenAI", "1", "Series D", "London / NY",
     "AI video for enterprises", "https://synthesia.io/careers", ""],
    ["Adlook", "Hypergrowth AdTech <5yr", "3", "Private", "NYC", "Cookieless adtech",
     "", ""],
    [],
    ["TIER LEGEND"],
    ["Tier 1", "Top priority. Active sweep + Apify search + headhunter mention."],
]


def test_his_list_is_read_and_the_legend_is_not_mistaken_for_a_company():
    """Reading one row too far turns "TIER LEGEND" into a company called
    TIER LEGEND and then sweeps for its job board."""
    got = targets.read(FakeSheet(HIS_TAB))
    assert [t.name for t in got] == ["TollBit", "Synthesia", "Adlook"]
    assert got[0].tier_number == 1 and got[2].tier_number == 3
    assert got[0].row == 3


def test_a_company_with_no_careers_url_is_simply_absent_from_the_map():
    urls = targets.careers_urls(FakeSheet(HIS_TAB))
    assert set(urls) == {"tollbit", "synthesia"}


def test_hunter_never_writes_into_the_columns_he_owns():
    """Columns A to H are his. A score written into his "Why" column would
    be worse than no score at all."""
    sheet = FakeSheet(HIS_TAB)
    before = [list(r) for r in sheet.grid]
    targets.write_hunter_columns(sheet, {3: [9.5, 1, "greenhouse:tollbit",
                                             "2026-09-20", 4, 2, 1, "evidence"]})
    def his(row):
        return [str(c) for c in (list(row) + [""] * targets.HIS_WIDTH)[:targets.HIS_WIDTH]]

    for i, row in enumerate(sheet.grid):
        old = before[i] if i < len(before) else []
        assert his(row) == his(old), f"row {i + 1} of his own columns changed"


def test_the_hunter_columns_are_read_back_after_the_write():
    sheet = FakeSheet(HIS_TAB)
    targets.write_hunter_columns(sheet, {3: [9.5, 1, "greenhouse:tollbit",
                                             "2026-09-20", 4, 2, 1, "why"]})
    back = sheet.read_tab_values("Target Companies!I3:P3")[0]
    assert str(back[0]) == "9.5" and str(back[1]) == "1"


def test_a_write_that_does_not_land_is_an_error_not_a_success():
    """A Sheets write can answer 200 and land nowhere if the grid moved."""
    from hunter.sheet import SheetError

    class Deaf(FakeSheet):
        def _write(self, blocks, raw=False):
            pass

    with pytest.raises(SheetError, match="did not land"):
        Deaf(HIS_TAB).write_hunter_columns if False else \
            targets.write_hunter_columns(Deaf(HIS_TAB), {3: [9.5, 1]})


def test_proposals_sit_below_his_list_and_never_inside_it():
    sheet = FakeSheet(HIS_TAB)
    targets.write_proposals(sheet, [("Mercor", 9.1, "ai native enterprise",
                                     "backed by Benchmark (https://x)")],
                            after_row=5)
    names = [r[0] if r else "" for r in sheet.grid]
    assert names[2:5] == ["TollBit", "Synthesia", "Adlook"]
    assert any(str(n).startswith("PROPOSED") for n in names)


def test_the_proposal_block_is_capped():
    """A list he does not read is worth less than none: it becomes another
    tab competing with the Pipeline for the attention that matters."""
    sheet = FakeSheet(HIS_TAB)
    many = [(f"Proposed{i}", 9.0, "cat", "evidence") for i in range(40)]
    targets.write_proposals(sheet, many, after_row=5)
    written = [r for r in sheet.grid if r and str(r[0]).startswith("Proposed")]
    assert len(written) == targets.MAX_PROPOSED


# ---------- the careers page, which is an answer rather than a guess ----------

class Resp:
    def __init__(self, text, ok=True):
        self.text, self.ok = text, ok
        self.status_code = 200 if ok else 404


@pytest.mark.parametrize("html,expect", [
    ('<a href="https://boards.greenhouse.io/tollbit">Jobs</a>',
     ("greenhouse", "tollbit")),
    ('<a href="https://jobs.ashbyhq.com/mercor/x">Open roles</a>',
     ("ashby", "mercor")),
    ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=descript">',
     ("greenhouse", "descript")),
    ('<a href="https://jobs.lever.co/someco/abc">Careers</a>',
     ("lever", "someco")),
])
def test_a_board_link_on_the_careers_page_resolves_it(monkeypatch, html, expect):
    from hunter.ats import discover as disc
    monkeypatch.setattr(disc.requests, "get", lambda *a, **k: Resp(html))
    assert from_careers_page("https://example.com/careers") == expect


def test_an_embed_path_is_never_mistaken_for_a_company_slug(monkeypatch):
    """Descript's careers page yielded the slug "embed", which probes a real
    and entirely unrelated Greenhouse board and would have reported its roles
    as Descript's."""
    from hunter.ats import discover as disc
    monkeypatch.setattr(disc.requests, "get", lambda *a, **k: Resp(
        '<iframe src="https://boards.greenhouse.io/embed/job_board?for=descript">'))
    assert from_careers_page("https://example.com/careers") == ("greenhouse", "descript")
    assert "embed" in NOT_A_SLUG


def test_a_careers_page_with_no_board_link_answers_none_rather_than_guessing(monkeypatch):
    from hunter.ats import discover as disc
    monkeypatch.setattr(disc.requests, "get", lambda *a, **k: Resp(
        "<p>Email us at jobs@example.com</p>"))
    assert from_careers_page("https://example.com/careers") is None


def test_discovery_prefers_the_careers_page_over_guessing_a_slug(monkeypatch):
    """Guessing turns "Sphere" into a tax software company. A link the
    company published cannot."""
    from hunter.ats import discover as disc
    monkeypatch.setattr(disc.requests, "get", lambda *a, **k: Resp(
        '<a href="https://jobs.ashbyhq.com/sphere-labs/x">Careers</a>'))
    probed = []
    monkeypatch.setattr(disc, "probe", lambda s, **k: probed.append(s))
    got = disc.discover(None, "Sphere", cache={},
                        careers_url="https://sphere.example/careers")
    assert got == ("ashby", "sphere-labs")
    assert not probed, "the careers page answered, so nothing should be guessed"

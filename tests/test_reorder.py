"""Career highlight reordering, which canon 9.12 has always required and
docbuild refused to do because delete-plus-insert drops bold runs.

The operation is allowed now because it re-applies the runs and then proves it.
These tests are the proof that the proof works: they run against a fake Docs API
that models the real failure mode, namely that inserted text arrives unstyled.
"""
from __future__ import annotations

import pytest

from hunter.docbuild import DocBuild


class FakeDocs:
    """A Docs document as a list of (text, [(start, end)] bold ranges relative to
    the paragraph). Insertion arrives UNSTYLED, exactly as the real API behaves,
    so a reorder that forgets to re-apply bold will fail these tests."""

    def __init__(self, paragraphs, bulleted=()):
        self.paras = [{"text": t if t.endswith("\n") else t + "\n",
                       "bolds": list(b),
                       "bullet": i in set(bulleted)}
                      for i, (t, b) in enumerate(paragraphs)]
        self.batches = 0

    # ---- the shape DocBuild.get/paragraphs expects ----
    def as_document(self) -> dict:
        content = []
        idx = 1
        for p in self.paras:
            elements = []
            text = p["text"]
            marks = [False] * len(text)
            for a, b in p["bolds"]:
                for i in range(a, min(b, len(text))):
                    marks[i] = True
            run_start = 0
            for i in range(1, len(text) + 1):
                if i == len(text) or marks[i] != marks[run_start]:
                    elements.append({
                        "startIndex": idx + run_start,
                        "endIndex": idx + i,
                        "textRun": {
                            "content": text[run_start:i],
                            "textStyle": ({"bold": True} if marks[run_start]
                                          else {})}})
                    run_start = i
            para = {"elements": elements}
            if p.get("bullet"):
                para["bullet"] = {"listId": "L1"}
            content.append({"paragraph": para,
                            "startIndex": idx,
                            "endIndex": idx + len(text)})
            idx += len(text)
        return {"body": {"content": content}}

    def offset_of(self, para_index: int) -> int:
        return 1 + sum(len(p["text"]) for p in self.paras[:para_index])

    def apply(self, reqs: list[dict]) -> None:
        self.batches += 1
        for r in reqs:
            if "deleteContentRange" in r:
                rng = r["deleteContentRange"]["range"]
                self._delete(rng["startIndex"], rng["endIndex"])
            elif "insertText" in r:
                self._insert(r["insertText"]["location"]["index"],
                             r["insertText"]["text"])
            elif "createParagraphBullets" in r:
                rng = r["createParagraphBullets"]["range"]
                self._bullet(rng["startIndex"], rng["endIndex"])
            elif "updateTextStyle" in r:
                rng = r["updateTextStyle"]["range"]
                bold = bool(r["updateTextStyle"]["textStyle"].get("bold"))
                if bold:
                    self._bold(rng["startIndex"], rng["endIndex"])

    def _bullet(self, s: int, e: int) -> None:
        """Bullets apply per paragraph whose range intersects the request."""
        idx = 1
        for p in self.paras:
            end = idx + len(p["text"])
            if idx < e and end > s:
                p["bullet"] = True
            idx = end

    def bullet_count(self) -> int:
        return sum(1 for p in self.paras if p.get("bullet"))

    def _flat(self) -> tuple[str, list[bool]]:
        text = "".join(p["text"] for p in self.paras)
        marks = []
        for p in self.paras:
            m = [False] * len(p["text"])
            for a, b in p["bolds"]:
                for i in range(a, min(b, len(p["text"]))):
                    m[i] = True
            marks.extend(m)
        return text, marks

    def _rebuild(self, text: str, marks: list[bool]) -> None:
        bullets_by_text = {}
        for p in self.paras:
            bullets_by_text.setdefault(p["text"], []).append(p.get("bullet", False))
        self.paras = []
        start = 0
        for i, ch in enumerate(text):
            if ch == "\n":
                seg, segm = text[start:i + 1], marks[start:i + 1]
                bolds, run = [], None
                for j, b in enumerate(segm):
                    if b and run is None:
                        run = j
                    elif not b and run is not None:
                        bolds.append((run, j))
                        run = None
                if run is not None:
                    bolds.append((run, len(segm)))
                queue = bullets_by_text.get(seg)
                flag = queue.pop(0) if queue else False
                self.paras.append({"text": seg, "bolds": bolds, "bullet": flag})
                start = i + 1

    def _delete(self, s: int, e: int) -> None:
        text, marks = self._flat()
        self._rebuild(text[:s - 1] + text[e - 1:], marks[:s - 1] + marks[e - 1:])

    def _insert(self, at: int, new: str) -> None:
        text, marks = self._flat()
        # Inserted text is UNSTYLED. This is the real failure mode.
        self._rebuild(text[:at - 1] + new + text[at - 1:],
                      marks[:at - 1] + [False] * len(new) + marks[at - 1:])

    def _bold(self, s: int, e: int) -> None:
        text, marks = self._flat()
        for i in range(s - 1, min(e - 1, len(marks))):
            marks[i] = True
        self._rebuild(text, marks)

    def bold_strings(self) -> list[str]:
        out = []
        for p in self.paras:
            for a, b in p["bolds"]:
                out.append(p["text"][a:b].strip())
        return sorted(x for x in out if x)

    def texts(self) -> list[str]:
        return [p["text"].rstrip("\n") for p in self.paras]


def make_db(fake: FakeDocs) -> DocBuild:
    db = DocBuild("token")
    db.get = lambda doc_id: fake.as_document()
    db.batch = lambda doc_id, reqs: fake.apply(reqs)
    return db


def bold_on(text: str, *fragments: str):
    """Offsets derived from the text, so the fixture cannot drift from the words
    it claims to bold. Krish's master bolds the numbers inside the prose."""
    spans = []
    for frag in fragments:
        at = text.index(frag)
        spans.append((at, at + len(frag)))
    return text, spans


# Krish's real highlights: three carry bold numbers, four carry none.
HIGHLIGHTS = [
    bold_on("Built APAC region from $0 to $12M ARR in three years at 22% EBITDA.",
            "$0 to $12M ARR", "22% EBITDA"),
    bold_on("Grew broadcaster data revenue from $9M to $61M in three years.",
            "$9M to $61M"),
    bold_on("Scaled a telco martech acquisition from $4M to $38M.",
            "$4M to $38M"),
    bold_on("Built AI engines and my own AI operating system."),
    bold_on("Run AI sessions with over 4000 leaders."),
    bold_on("Prolific podcaster, writer and content creator."),
    bold_on("Huge networks across USA, UK and Australia."),
]


def test_the_fake_models_the_real_failure_mode():
    """Sanity check on the harness: an insert must arrive unstyled, or these
    tests would pass even for a broken implementation."""
    fake = FakeDocs(HIGHLIGHTS)
    before = fake.bold_strings()
    fake.apply([{"insertText": {"location": {"index": 1}, "text": "New line.\n"}}])
    assert fake.bold_strings() == before
    assert fake.texts()[0] == "New line."


def test_reorder_moves_the_paragraphs():
    fake = FakeDocs(HIGHLIGHTS)
    db = make_db(fake)
    order = [4, 0, 1, 2, 3, 5, 6]
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS], order)
    assert fake.texts() == [HIGHLIGHTS[i][0] for i in order]


def test_reorder_preserves_the_exact_bolded_substrings():
    """The assertion that earns the operation: not the count, the strings."""
    fake = FakeDocs(HIGHLIGHTS)
    expected = fake.bold_strings()
    db = make_db(fake)
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                          [6, 5, 4, 3, 2, 1, 0])
    assert fake.bold_strings() == expected
    assert "$0 to $12M" in " ".join(fake.bold_strings())


def test_bold_lands_on_the_same_words_not_just_the_same_count():
    """A reorder that re-applied bold at the OLD offsets would keep the count and
    bold the wrong words. That is the bug this test exists for."""
    fake = FakeDocs(HIGHLIGHTS)
    db = make_db(fake)
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                          [3, 4, 5, 6, 0, 1, 2])
    original = {t[a:b] for t, spans in HIGHLIGHTS for a, b in spans}
    for p in fake.paras:
        for a, b in p["bolds"]:
            fragment = p["text"][a:b]
            assert fragment in original, (
                f"bold landed on {fragment!r}, which was never bolded in the "
                f"master; re-applying at the old offsets would do exactly this")


def test_an_identity_reorder_changes_nothing():
    fake = FakeDocs(HIGHLIGHTS)
    before_text, before_bold = fake.texts(), fake.bold_strings()
    db = make_db(fake)
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                          list(range(len(HIGHLIGHTS))))
    assert fake.texts() == before_text and fake.bold_strings() == before_bold


def test_a_non_permutation_is_refused_before_any_write():
    fake = FakeDocs(HIGHLIGHTS)
    db = make_db(fake)
    with pytest.raises(ValueError, match="permutation"):
        db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                              [0, 0, 1, 2, 3, 4, 5])
    assert fake.batches == 0, "nothing may be written on a bad order"


def test_a_missing_anchor_is_refused_before_any_write():
    fake = FakeDocs(HIGHLIGHTS)
    db = make_db(fake)
    anchors = [h[:60] for h, _ in HIGHLIGHTS]
    anchors[2] = "a highlight that does not exist"
    with pytest.raises(LookupError):
        db.reorder_paragraphs("doc", anchors, [1, 0, 2, 3, 4, 5, 6])
    assert fake.batches == 0


def test_non_contiguous_paragraphs_are_refused():
    """Reordering across a heading would move unrelated content."""
    paras = HIGHLIGHTS[:2] + [("CORE COMPETENCIES", [])] + HIGHLIGHTS[2:4]
    fake = FakeDocs(paras)
    db = make_db(fake)
    anchors = [HIGHLIGHTS[0][0][:60], HIGHLIGHTS[1][0][:60],
               HIGHLIGHTS[2][0][:60]]
    with pytest.raises(RuntimeError, match="not contiguous"):
        db.reorder_paragraphs("doc", anchors, [2, 0, 1])


def test_a_reorder_that_loses_bold_raises_rather_than_shipping():
    """If the re-apply step were dropped, the read-back must catch it."""
    fake = FakeDocs(HIGHLIGHTS)
    db = make_db(fake)
    real_apply = fake.apply

    def drop_styles(reqs):
        real_apply([r for r in reqs if "updateTextStyle" not in r])

    db.batch = lambda doc_id, reqs: drop_styles(reqs)
    with pytest.raises(RuntimeError, match="lost or moved bold runs"):
        db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                              [1, 0, 2, 3, 4, 5, 6])


# ---------------- the summary block replacement ----------------

SUMMARY = [
    ("PROFESSIONAL SUMMARY", [(0, 20)]),
    ("With 16 years commercializing data and tech, I have scaled to 9 figures.", []),
    ("I have built commercial divisions across Microsoft, Nine and SingTel.", []),
    ("The time is right for me to shift my attentions.", []),
    ("CAREER HIGHLIGHTS", [(0, 17)]),
]


def test_the_whole_summary_span_is_replaced_by_one_block():
    """Replacing only paragraph 1 left the master's own two behind and shipped a
    CV in two registers. This is the fix."""
    fake = FakeDocs(SUMMARY)
    db = make_db(fake)
    db.replace_paragraph_block("doc", "With 16 years", 3,
                               "One coherent summary, written for this role.")
    texts = fake.texts()
    assert texts[0] == "PROFESSIONAL SUMMARY"
    assert texts[1] == "One coherent summary, written for this role."
    assert "The time is right" not in " ".join(texts)
    assert texts[2] == "CAREER HIGHLIGHTS"


def test_the_block_replacement_refuses_to_touch_bolded_paragraphs():
    """So it can never be aimed at the employer entries or the highlights."""
    fake = FakeDocs(HIGHLIGHTS)
    db = make_db(fake)
    with pytest.raises(RuntimeError, match=r"carries \d+ bold run"):
        db.replace_paragraph_block("doc", "Built APAC region", 2, "replacement")
    assert fake.batches == 0


def test_the_block_replacement_refuses_a_span_that_runs_off_the_end():
    fake = FakeDocs(SUMMARY)
    db = make_db(fake)
    with pytest.raises(RuntimeError, match="needed 99"):
        db.replace_paragraph_block("doc", "With 16 years", 99, "replacement")


# ---------------- same-day rebuilds must not deadlock ----------------

def test_a_same_day_rebuild_gets_a_new_name_instead_of_failing():
    """build_package builds the letter before the CV and is not atomic, so
    anything that raises in the CV leaves the letter behind and the retry has
    nowhere to land. That killed three consecutive attempts on the first live
    Harvey build, and the docstring already recorded the same shape for Cohere."""
    import datetime

    from hunter.package.build import _unique_title

    taken = {
        "KrishRaja_CV_Harvey",
        "KrishRaja_CV_Harvey_head-of-gtm",
        "KrishRaja_CV_Harvey_head-of-gtm_20260914",
    }

    class FakeDrive:
        def find_by_name(self, name, parent_id):
            return [{"id": "x"}] if name in taken else []

    got = _unique_title(FakeDrive(), "KrishRaja_CV_Harvey", "folder",
                        "head-of-gtm", today=datetime.date(2026, 9, 14))
    assert got == "KrishRaja_CV_Harvey_head-of-gtm_20260914_02"
    taken.add(got)
    got2 = _unique_title(FakeDrive(), "KrishRaja_CV_Harvey", "folder",
                         "head-of-gtm", today=datetime.date(2026, 9, 14))
    assert got2 == "KrishRaja_CV_Harvey_head-of-gtm_20260914_03"


def test_a_runaway_retry_loop_is_refused_rather_than_piling_up_copies():
    import datetime

    from hunter.package.build import BuildError, _unique_title

    class AlwaysTaken:
        def find_by_name(self, name, parent_id):
            return [{"id": "x"}]

    with pytest.raises(BuildError, match="retrying in a loop"):
        _unique_title(AlwaysTaken(), "KrishRaja_CV_Harvey", "folder", "role",
                      today=datetime.date(2026, 9, 14))


def test_the_suffix_is_not_a_cv_version_number():
    """Canon 9.12 forbids a version number on a per-role copy. The suffix counts
    same-day attempts at one role, and the master's own version is untouched."""
    import datetime

    from hunter.package.build import _unique_title

    class OneTaken:
        def find_by_name(self, name, parent_id):
            return [{"id": "x"}] if name == "KrishRaja_CV_Harvey" else []

    got = _unique_title(OneTaken(), "KrishRaja_CV_Harvey", "folder", "role",
                        today=datetime.date(2026, 9, 14))
    assert got == "KrishRaja_CV_Harvey_role"
    assert "v1" not in got and "v14" not in got


# ---------------- bullets must survive the reorder ----------------

def test_reorder_preserves_bullet_formatting():
    """insertText creates PLAIN paragraphs. The first real Harvey build reordered
    the highlights correctly and silently stripped the bullet from all seven; the
    package verifier caught it as "expected 26 bullets, found 19"."""
    fake = FakeDocs(HIGHLIGHTS, bulleted=range(len(HIGHLIGHTS)))
    assert fake.bullet_count() == len(HIGHLIGHTS)
    db = make_db(fake)
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                          [2, 0, 1, 3, 4, 5, 6])
    assert fake.bullet_count() == len(HIGHLIGHTS), "a bullet was lost"


def test_a_reorder_that_loses_bullets_raises_rather_than_shipping():
    fake = FakeDocs(HIGHLIGHTS, bulleted=range(len(HIGHLIGHTS)))
    db = make_db(fake)
    real_apply = fake.apply

    def drop_bullets(reqs):
        real_apply([r for r in reqs if "createParagraphBullets" not in r])

    db.batch = lambda doc_id, reqs: drop_bullets(reqs)
    with pytest.raises(RuntimeError, match="lost bullet formatting"):
        db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                              [1, 0, 2, 3, 4, 5, 6])


def test_unbulleted_paragraphs_do_not_gain_a_bullet():
    fake = FakeDocs(HIGHLIGHTS)  # none bulleted
    db = make_db(fake)
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                          [6, 5, 4, 3, 2, 1, 0])
    assert fake.bullet_count() == 0


def test_a_mixed_block_keeps_exactly_the_bullets_it_had():
    fake = FakeDocs(HIGHLIGHTS, bulleted=(0, 1, 2))
    db = make_db(fake)
    db.reorder_paragraphs("doc", [h[:60] for h, _ in HIGHLIGHTS],
                          [3, 4, 5, 6, 0, 1, 2])
    assert fake.bullet_count() == 3

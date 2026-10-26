"""The judge's record, encoded, so a plainly wrong role costs no call.

His words, 2026-10-26: "I would rather encode as much as possible into
followable, replicable steps rather than as we go, so the cost should keep
coming down and down and down." Until then every role that cleared the hard
gates went to one Opus call, and 82 percent of those calls came back a
rejection at fit about 2: a seat in a function he has never taken, a domain he
has never worked in. Nothing learned from the 933 roles the judge had already
read with their postings, or from his scores.

The screen is a logistic model over the words of the title and the posting,
fitted on every role the judge ruled on, with his own record above it: a role
he said Yes to, applied to or scored 7 or more is a role the screen must never
cut, whatever the judge said. It is refitted from the record on every run, so
each judgement and each score he types make the next run cheaper.

The line is chosen so that, scored out of fold, it would have cut none of the
roles the judge presented and none he wanted. A role under the line is
rejected with no call and a reason that says so; everything else goes to the
judge as before. Pure Python, like rankscore.py: no new dependency.

The weights name companies and posting words beside his choices, so they live
in system_config (CONFIG_KEY), never in this public repo.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import re

CONFIG_KEY = "hunter_screen_weights"
MODE_KEY = "hunter_screen_mode"
AUDIT_KEY = "hunter_screen_audit_per_run"
AUDIT_PER_RUN = 2
VERSION = "2026-10-26.2"

# How much of a posting is read. The opening says what the seat is; the long
# tail is benefits and equal opportunity text that every posting shares.
POSTING_CHARS = 6000
# A word must appear in this many training roles to carry a weight, so one
# odd posting cannot teach the screen a rule.
MIN_DOCS = 3
EPOCHS = 30
RATE = 0.2
L2 = 1e-3
FOLDS = 5
SEED = 7

TOKEN = re.compile(r"[a-z][a-z0-9+#&]+")
STOP = frozenset("""
a an and are as at be by for from has have in is it its of on or our that the
this to we with you your will who what when where how all any can into more
not their they them us was were which while also about over such than other
""".split())


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall((text or "").lower()) if t not in STOP]


def company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def features(*, title: str, posting: str = "", company: str = "") -> set[str]:
    """The words a role is judged on: the company, whose other roles the judge
    has already read; the title's words and pairs, which say what the seat
    is; and the posting's words, which say what it does."""
    t = tokens(title)
    f = {"t:" + w for w in t}
    if company_key(company):
        f.add("c:" + company_key(company))
    f |= {f"tb:{a}_{b}" for a, b in zip(t, t[1:])}
    f |= {"p:" + w for w in tokens((posting or "")[:POSTING_CHARS])}
    return f


# ---------- labels ----------

def label(*, judge_verdict: str | None, his: str | None) -> int | None:
    """1 for a role worth a judge's reading, 0 for one the judge rejected,
    None for one with nothing to learn from.

    his is "want" (Yes, Applied, or a Krish Score of 7 or more), "no" or None.
    His want wins over a judge rejection: the screen must never cut a seat he
    asked for. His no does not turn a judge presentation into a 0, because his
    declines are mostly about the business, and the screen reads the seat.
    """
    if his == "want":
        return 1
    if judge_verdict == "present":
        return 1
    if judge_verdict == "reject":
        return 0
    return None


# ---------- the model ----------

def _vocab(docs: list[set[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in docs:
        for f in d:
            counts[f] = counts.get(f, 0) + 1
    names = sorted(f for f, n in counts.items() if n >= MIN_DOCS)
    return {f: i for i, f in enumerate(names)}


def _sig(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def fit(docs: list[set[str]], ys: list[int], *, epochs: int = EPOCHS,
        seed: int = SEED) -> dict:
    """Logistic regression by AdaGrad on binary features. Positives are
    weighted up to the size of the negatives, because a missed positive is
    the costly mistake."""
    vocab = _vocab(docs)
    w = [0.0] * len(vocab)
    g2 = [1e-8] * len(vocab)
    b, gb = 0.0, 1e-8
    pos = sum(ys) or 1
    neg = (len(ys) - sum(ys)) or 1
    weight = {1: neg / pos, 0: 1.0}
    rows = [([vocab[f] for f in d if f in vocab], y) for d, y in zip(docs, ys)]
    # Each role's features are scaled by one over the root of how many it
    # has, so a long posting does not shout a short one down.
    rows = [(idx, y, 1.0 / math.sqrt(max(1, len(idx)))) for idx, y in rows]
    rng = random.Random(seed)
    for _ in range(epochs):
        rng.shuffle(rows)
        for idx, y, x in rows:
            p = _sig(b + x * sum(w[i] for i in idx))
            err = (p - y) * weight[y]
            gb += err * err
            b -= RATE * err / math.sqrt(gb)
            for i in idx:
                g = err * x + L2 * w[i]
                g2[i] += g * g
                w[i] -= RATE * g / math.sqrt(g2[i])
    names = {i: f for f, i in vocab.items()}
    # Every word in the vocabulary is kept, even at a weight near zero: the
    # scale in score() counts them, and must count what fit() counted.
    return {"bias": b, "w": {names[i]: round(x, 5) for i, x in enumerate(w)}}


def score(model: dict, doc: set[str]) -> float:
    """The probability the screen gives that the judge would want this read."""
    w = model.get("w") or {}
    hits = [w[f] for f in doc if f in w]
    x = 1.0 / math.sqrt(max(1, len(hits)))
    return _sig(model.get("bias", 0.0) + x * sum(hits))


def out_of_fold(docs: list[set[str]], ys: list[int], *, folds: int = FOLDS,
                seed: int = SEED) -> list[float]:
    """Each role scored by a model that never saw it."""
    order = list(range(len(docs)))
    random.Random(seed).shuffle(order)
    scores = [0.0] * len(docs)
    for k in range(folds):
        held = set(order[k::folds])
        train = [i for i in order if i not in held]
        m = fit([docs[i] for i in train], [ys[i] for i in train], seed=seed + k)
        for i in held:
            scores[i] = score(m, docs[i])
    return scores


def line(scores: list[float], ys: list[int], *, margin: float = 0.5) -> float:
    """The cut: below every positive's out-of-fold score, less a margin taken
    as a share of the lowest positive score. A screen that would have cut a
    positive in its own cross validation does not cut at all."""
    pos = [s for s, y in zip(scores, ys) if y == 1]
    if not pos:
        return 0.0
    return min(pos) * (1.0 - margin)


def build(rows: list[dict]) -> dict:
    """The stored screen, from labelled rows {title, posting, label}."""
    rows = [r for r in rows if r.get("label") in (0, 1)]
    docs = [features(title=r["title"], posting=r.get("posting", ""),
                     company=r.get("company", "")) for r in rows]
    ys = [r["label"] for r in rows]
    if sum(ys) < 10 or len(ys) - sum(ys) < 10:
        return {}
    oof = out_of_fold(docs, ys)
    cut = line(oof, ys)
    model = fit(docs, ys)
    would = sum(1 for s, y in zip(oof, ys) if s < cut and y == 0)
    return {**model, "line": cut, "version": VERSION, "trained_on": len(ys),
            "positives": sum(ys), "would_cut_of_negatives": would,
            "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def screened(model: dict, *, title: str, posting: str, company: str = "") -> tuple[bool, float]:
    """(under the line, score) for one role. No model means nothing is cut."""
    if not model or not model.get("w"):
        return False, 1.0
    s = score(model, features(title=title, posting=posting, company=company))
    return s < model.get("line", 0.0), s


# ---------- in the run ----------

def mode_of(cfg) -> str:
    """shadow until he says otherwise: it learns and reports what it would cut,
    and every role still goes to the judge."""
    m = (cfg.optional(MODE_KEY, "shadow") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "gate") else "shadow"


WANT_VERDICTS = ("go", "applied")
# Every application_state that means he applied: the two the apply path
# writes (run.APPLIED_STATES) and what happened after (actions.OUTCOMES).
WANT_STATES = ("Applied", "submitted", "Interviewing", "Offer", "Rejected by employer",
               "Withdrew")
FIELDS = ("job_id,company,title,jd_text,judge_verdict,judge_at,presented_at,scanned_at,"
          "application_state,krish_verdict")


def record(cfg) -> list[dict]:
    """Every role the judge ruled on, and every role he wanted whether the
    judge read it or not, labelled, his record above the judge's. A role he
    wanted that the judge never read is a positive too: the line must sit
    under it as well."""
    from .config import ALL_ROWS, db_get
    roles = {r["job_id"]: r for r in db_get(cfg, "hunter_seen_roles", {
        "select": FIELDS, "judge_at": "not.is.null", "limit": ALL_ROWS})}
    for r in db_get(cfg, "hunter_seen_roles", {
            "select": FIELDS, "application_state": f"in.({','.join(WANT_STATES)})",
            "limit": ALL_ROWS}):
        roles.setdefault(r["job_id"], r)
    want = {e["job_id"] for e in db_get(cfg, "hunter_verdict_events", {
        "select": "job_id", "verdict": f"in.({','.join(WANT_VERDICTS)})", "limit": ALL_ROWS})}
    for r in db_get(cfg, "hunter_krish_ratings", {
            "select": "job_id,kind,score", "kind": "in.(scored,yes_unscored)",
            "limit": ALL_ROWS}):
        if r.get("kind") == "yes_unscored" or (r.get("score") or 0) >= 7:
            want.add(r["job_id"])
    missing = sorted(j for j in want if j and j not in roles)
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        for r in db_get(cfg, "hunter_seen_roles", {
                "select": FIELDS, "job_id": "in.(" + ",".join(f'"{j}"' for j in chunk) + ")",
                "limit": ALL_ROWS}):
            roles.setdefault(r["job_id"], r)
    rows = []
    for r in roles.values():
        kv = (r.get("krish_verdict") or "").strip().lower()
        his = "want" if (r["job_id"] in want or r.get("application_state") in WANT_STATES
                         or kv.startswith(("yes", "applied"))) else None
        y = label(judge_verdict=r.get("judge_verdict"), his=his)
        if y is not None:
            rows.append({"title": r.get("title") or "", "posting": r.get("jd_text") or "",
                         "company": r.get("company") or "", "label": y,
                         "at": (r.get("judge_at") or r.get("presented_at")
                                or r.get("scanned_at") or ""),
                         "want": his == "want",
                         "present": r.get("judge_verdict") == "present"})
    return rows


# ---------- the measurement ----------

def cutoffs(rows: list[dict], shares=(0.5, 0.6, 0.7, 0.8)) -> list[str]:
    """Dates splitting the record at these shares, oldest first, so the
    windows move with the record instead of being fixed on one day."""
    ats = sorted(r["at"] for r in rows if r.get("at"))
    return [ats[int(len(ats) * q)] for q in shares] if ats else []


def evaluate(rows: list[dict], cutoffs: list[str]) -> dict:
    """Fit on what was judged before each cutoff and screen what came after,
    the way it would have run. Counts only: this goes in the public repo.

    The bar: on every window it cuts none of the roles the judge presented
    and none he wanted. The share it cuts is what it saves; it should grow as
    the record grows."""
    rows = sorted((r for r in rows if r.get("label") in (0, 1) and r.get("at")),
                  key=lambda r: r["at"])
    windows = []
    for cut_at in cutoffs:
        train = [r for r in rows if r["at"] < cut_at]
        test = [r for r in rows if r["at"] >= cut_at]
        model = build(train)
        if not model or not test:
            continue
        under = [r for r in test if screened(model, title=r["title"], posting=r["posting"],
                                             company=r.get("company", ""))[0]]
        windows.append({
            "from": cut_at[:10], "trained_on": model["trained_on"], "screened_of": len(test),
            "screened": len(under), "share": round(len(under) / len(test), 3),
            "judge_rejects_cut": sum(1 for r in under if r["label"] == 0),
            "presents_lost": sum(1 for r in under if r.get("present")),
            "wants_lost": sum(1 for r in under if r.get("want"))})
    return {"version": VERSION, "measured": dt.date.today().isoformat(),
            "labelled": len(rows), "windows": windows}


def _read(cfg) -> dict:
    from .config import db_get
    got = db_get(cfg, "system_config", {"key": f"eq.{CONFIG_KEY}", "select": "value"})
    if not got:
        return {}
    raw = got[0].get("value")
    try:
        model = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return model if isinstance(model, dict) else {}


def store(cfg, model: dict) -> None:
    """Write the screen to system_config and read it back."""
    from .config import db_get, db_insert, db_patch
    value = json.dumps(model)
    if db_get(cfg, "system_config", {"key": f"eq.{CONFIG_KEY}", "select": "key"}):
        db_patch(cfg, "system_config", {"key": CONFIG_KEY}, {"value": value})
    else:
        db_insert(cfg, "system_config", [{"key": CONFIG_KEY, "value": value}])
    back = _read(cfg)
    if back.get("fitted_at") != model.get("fitted_at") or back.get("line") != model.get("line"):
        raise RuntimeError("the screen did not read back")


def refresh(cfg) -> tuple[dict, str]:
    """(model, how) refitted from today's record, or the stored one when the
    record cannot be read. Refitting every run is what makes it compound."""
    try:
        model = build(record(cfg))
        if model:
            store(cfg, model)
            return model, "refitted"
    except Exception as e:
        try:
            return _read(cfg), f"stored, refit failed ({e.__class__.__name__})"
        except Exception:
            return {}, f"none, refit failed ({e.__class__.__name__})"
    return {}, "none, too little record to fit"


def exempt(c, top_keys: set) -> str:
    """Why a candidate is never screened, or ''. The same exceptions the
    company bar keeps, plus his list and a posting he sent himself."""
    from . import companybar, universe
    if universe.key_of(c.role.company) in top_keys:
        return "a company on his list"
    if companybar.AI_SEAT.search(c.role.title or ""):
        return "an AI transformation seat"
    if companybar.RECRUITER.search(c.role.company or ""):
        return "a recruiter's posting"
    from .sheet import SENT_SOURCE
    if (getattr(c.role, "source", "") or "") == SENT_SOURCE:
        return "a posting he sent"
    return ""


def apply(cfg, candidates: list, *, top_keys: set, mark: bool,
          model: dict | None = None) -> tuple[list, list, list[str]]:
    """(kept, the ones to report on, summary lines).

    In gate mode (and mark) a role under the line is blocked with no judge
    call, except an audit sample that still goes to the judge and is what
    comes back to report on. In shadow mode nothing is cut: every candidate
    goes on to the judge and the ones under the line come back so the run
    can say how the judge ruled on them."""
    mode = mode_of(cfg)
    if mode == "off" or not candidates:
        return candidates, [], []
    how = "given"
    if model is None:
        model, how = refresh(cfg)
    if not model:
        return candidates, [], [f"screen ({mode}): no screen, {how}"]
    under = []
    for c in candidates:
        if exempt(c, top_keys):
            continue
        cut, s = screened(model, title=c.role.title, posting=c.role.jd_text or "",
                          company=c.role.company)
        if cut:
            c.screen_score = s
            under.append(c)
    lines = [f"screen ({mode}, {how} on {model.get('trained_on', 0)} roles): "
             f"{len(under)} of {len(candidates)} under the line"
             + (", cut without a judge call" if mode == "gate" and mark else
                ", all still judged")]
    if mode == "gate" and mark:
        # A few of what it would cut still go to the judge, at random, so what
        # the gate hides is measured, as the judge's own audit sample is.
        n = min(len(under), int(cfg.optional(AUDIT_KEY, str(AUDIT_PER_RUN)) or 0))
        audit = random.sample(under, n) if n else []
        for c in audit:
            c.screen_audit = True
        if audit:
            lines.append(f"screen: {len(audit)} of them sent to the judge as an audit")
        under = [c for c in under if not c.screen_audit]
        ids = {id(c) for c in under}
        for c in under:
            c.row["status"] = "blocked"
            c.row["rejection_reason"] = (
                "SCREEN: the judge's record says this seat is not his kind "
                f"({c.screen_score:.3f} under {model.get('line', 0):.3f})")[:300]
        return [c for c in candidates if id(c) not in ids], audit, lines
    return candidates, under, lines


def shadow_report(under: list) -> list[str]:
    """After the judge ran: how it ruled on what the screen would have cut.
    A presented role here is a role the gate would have hidden."""
    ruled = [c for c in under if getattr(c, "judgement", None) is not None]
    if not ruled:
        return []
    rej = sum(1 for c in ruled if c.judgement.verdict == "reject")
    shown = [c for c in ruled if c.judgement.verdict in ("present", "hold")]
    lines = [f"screen shadow: of {len(under)} it would have cut, the judge rejected {rej}, "
             f"presented or held {len(shown)}"]
    for c in shown:
        lines.append(f"  screen would have hidden: {c.role.company} / {c.role.title}")
    return lines

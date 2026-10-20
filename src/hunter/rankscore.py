"""The order he reads Pipeline in, learned from his own scores.

His words, 2026-10-20, sending four roles hunter had missed: "I feel at this
stage we need to create a more sophisticated algorithmic scoring mechanism."
Measured on 2026-10-11, the AI Score (score.py) ranked the roles he scored at
Spearman -0.19 against his own scores, and the judge's fit at about 0: what
reached him was ordered no better than chance. score.py is a hand-written
point table; nothing in it was ever fitted to him.

This is a small, readable model fitted to his Krish Scores: a weight per
signal, every signal one he named or one the record already carries, read
from the posting and the judge's read, never from anything he wrote about the
role. It replaces nothing until it beats the AI Score on scores it was not
fitted on (tests/test_rank_eval.py); his ruling that day was "measure first".

Pure Python, no dependencies, so the tests stay offline. The fitted weights
are kept in system_config (hunter_rank_weights), never in this public repo.
"""
from __future__ import annotations

import json
import math
import re

# The signals, each 0 or 1 unless said. The names are what a weight is
# reported under; the order is the vector's.
NYC = re.compile(r"\bnew york\b|\bnyc\b|\bbrooklyn\b|\bmanhattan\b", re.I)
LONDON = re.compile(r"\blondon\b|\bunited kingdom\b|\buk\b", re.I)
REMOTE = re.compile(r"\bremote\b", re.I)
IN_PERSON = re.compile(r"\bhybrid\b|\bon.?site\b|\bin.office\b|\bin office\b", re.I)
FIRST = re.compile(r"\bfounding\b|\bfirst (?:\w+ ){0,3}hire\b|\bour first\b|\bthe first\b"
                   r"|\bbuild (?:and own )?the function\b|\bfrom scratch\b|\b0.to.1\b"
                   r"|\bzero.to.one\b", re.I)
TO_FOUNDER = re.compile(r"\breport(?:s|ing)? (?:directly )?to (?:the |our )?"
                        r"(?:co.?founder|founder|ceo|president)\b"
                        r"|\bto (?:the |our )?co.?founder\b|\bpartner(?:s|ing)? (?:very )?closely "
                        r"with (?:our |the )?(?:co.?founder|ceo)\b", re.I)
SENIOR = re.compile(r"\bchief\b|\bhead of\b|\bvp\b|\bvice president\b|\bsvp\b|\bevp\b"
                    r"|\bgeneral manager\b|\bmanaging director\b", re.I)
AI_NATIVE = re.compile(r"\bagentic\b|\bai.native\b|\bllms?\b|\bfoundation models?\b"
                       r"|\bgenerative ai\b|\bai agents?\b|\bai platform\b", re.I)
FAMILIES = ("country_regional_gm", "chief_commercial_strategy", "corp_dev_strategy",
            "partnerships_alliances", "ai_chief_of_staff_transformation")
MONEY = re.compile(r"\$\s?(\d{2,3})(?:,(\d{3}))?\s?(k)?", re.I)

NAMES = (["new_york", "london", "remote_only", "in_person", "first_of_role",
          "reports_to_founder", "senior_title", "ai_native", "pay_top_250k", "pay_shown",
          "judge_fit", "judge_read"]
         + [f"family_{f}" for f in FAMILIES])


def _top_pay(text: str) -> int:
    best = 0
    for m in MONEY.finditer(text or ""):
        n = int(m.group(1))
        val = n * 1000 if (m.group(3) or not m.group(2)) else int(m.group(1) + m.group(2))
        if 80_000 <= val <= 2_000_000:
            best = max(best, val)
    return best


def features(*, title: str, location: str, comp: str, jd_text: str, judge_answers=None,
             judge_fit=None) -> list[float]:
    """The signal vector for one role. Unknown is 0 with its own flag where
    unknown means something different (judge_read)."""
    from .package import proof
    text = f"{title}\n{location}\n{jd_text or ''}"
    where = f"{location} {(jd_text or '')[:3000]}"
    fam = proof.family_of(judge_answers, title)
    pay = _top_pay(f"{comp} {(jd_text or '')}")
    fit = None
    try:
        fit = float(judge_fit) if judge_fit not in (None, "") else None
    except (TypeError, ValueError):
        fit = None
    v = [
        1.0 if NYC.search(where) else 0.0,
        1.0 if LONDON.search(location or "") else 0.0,
        1.0 if REMOTE.search(location or "") and not NYC.search(location or "") else 0.0,
        1.0 if IN_PERSON.search(where) else 0.0,
        1.0 if FIRST.search(text) else 0.0,
        1.0 if TO_FOUNDER.search(jd_text or "") else 0.0,
        1.0 if SENIOR.search(title or "") else 0.0,
        1.0 if AI_NATIVE.search(jd_text or "") else 0.0,
        1.0 if pay >= 250_000 else 0.0,
        1.0 if pay else 0.0,
        (fit or 0.0) / 10.0,
        1.0 if fit is not None else 0.0,
    ]
    v += [1.0 if fam == f else 0.0 for f in FAMILIES]
    return v


# ---------- fitting: ridge regression, by hand ----------

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit(xs: list[list[float]], ys: list[float], ridge: float = 3.0) -> dict:
    """Weights and an intercept, the intercept unpenalised."""
    k = len(xs[0])
    mean_y = sum(ys) / len(ys)
    # Centre y so the intercept is the mean and the ridge pulls weights to 0.
    means = [sum(x[i] for x in xs) / len(xs) for i in range(k)]
    xc = [[x[i] - means[i] for i in range(k)] for x in xs]
    xtx = [[sum(x[i] * x[j] for x in xc) + (ridge if i == j else 0.0) for j in range(k)]
           for i in range(k)]
    xty = [sum(x[i] * (y - mean_y) for x, y in zip(xc, ys)) for i in range(k)]
    w = _solve(xtx, xty)
    b = mean_y - sum(wi * mi for wi, mi in zip(w, means))
    return {"weights": dict(zip(NAMES, w)), "intercept": b, "ridge": ridge, "n": len(ys)}


# The signals he named on 2026-10-20 as reasons he wants a role. A fit that
# gives one of them a negative weight is reading noise in 61 scores against
# his own words, so it is dropped from the fit instead (its weight 0). On the
# held-out scores this cost 0.03 of Spearman.
HIS_SIGNALS = ("new_york", "in_person", "first_of_role", "reports_to_founder",
               "ai_native", "senior_title")
RIDGE = 3.0


def fit_his(xs: list[list[float]], ys: list[float], ridge: float = RIDGE) -> dict:
    """fit, with no signal he named allowed to count against a role."""
    names = list(NAMES)
    drop: set[str] = set()
    model: dict = {}
    for _ in range(len(HIS_SIGNALS) + 1):
        idx = [i for i, n in enumerate(names) if n not in drop]
        sub = _fit_named([[x[i] for i in idx] for x in xs], ys, [names[i] for i in idx], ridge)
        neg = {n for n in HIS_SIGNALS if sub["weights"].get(n, 0.0) < 0}
        model = sub
        if not neg:
            break
        drop |= neg
    w = {n: 0.0 for n in names}
    w.update(model["weights"])
    return {"weights": w, "intercept": model["intercept"], "ridge": ridge, "n": len(ys),
            "held_at_zero": sorted(drop)}


def _fit_named(xs, ys, names, ridge):
    k = len(names)
    mean_y = sum(ys) / len(ys)
    means = [sum(x[i] for x in xs) / len(xs) for i in range(k)]
    xc = [[x[i] - means[i] for i in range(k)] for x in xs]
    xtx = [[sum(x[i] * x[j] for x in xc) + (ridge if i == j else 0.0) for j in range(k)]
           for i in range(k)]
    xty = [sum(x[i] * (y - mean_y) for x, y in zip(xc, ys)) for i in range(k)]
    w = _solve(xtx, xty)
    return {"weights": dict(zip(names, w)),
            "intercept": mean_y - sum(wi * mi for wi, mi in zip(w, means))}


def shown(model: dict | None, fallback: int, **kw) -> int:
    """The AI Score he is shown: the fitted model's, 1 to 10, when one is
    switched on; the old score.py number otherwise."""
    if not model:
        return fallback
    return max(1, min(10, round(predict(model, features(**kw)))))


def predict(model: dict, x: list[float]) -> float:
    w = model["weights"]
    return model["intercept"] + sum(w.get(n, 0.0) * xi for n, xi in zip(NAMES, x))


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else 0.0


CONFIG_KEY = "hunter_rank_weights"


_LOADED: dict = {}


def load(cfg) -> dict | None:
    """The fitted model he switched on, or None: then the AI Score orders.
    Read once per process: regate asks for it once per row."""
    if id(cfg) in _LOADED:
        return _LOADED[id(cfg)]
    _LOADED[id(cfg)] = model = _read(cfg)
    return model


def _read(cfg) -> dict | None:
    from .config import db_get
    try:
        got = db_get(cfg, "system_config", {"key": f"eq.{CONFIG_KEY}", "select": "value"})
    except Exception:
        return None
    if not got:
        return None
    raw = got[0].get("value")
    try:
        model = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return model if isinstance(model, dict) and model.get("weights") else None


def store(cfg, model: dict) -> None:
    """Switch a model on, and read it back."""
    from .config import db_get, db_insert, db_patch
    value = json.dumps(model)
    if db_get(cfg, "system_config", {"key": f"eq.{CONFIG_KEY}", "select": "key"}):
        db_patch(cfg, "system_config", {"key": CONFIG_KEY}, {"value": value})
    else:
        db_insert(cfg, "system_config", [{"key": CONFIG_KEY, "value": value}])
    _LOADED.clear()
    back = _read(cfg)
    if not back or back.get("weights") != model["weights"]:
        raise RuntimeError("the model did not read back")


def holdout(rows: list[dict], share: float = 0.65) -> dict:
    """Fit on the first share of his scores, oldest first, and measure on
    the rest: the bar a model must clear before it is switched on. rows carry
    x (features), his, ai_score and judge_fit."""
    cut = int(len(rows) * share)
    train, test = rows[:cut], rows[cut:]
    model = fit_his([r["x"] for r in train], [float(r["his"]) for r in train])
    his = [float(r["his"]) for r in test]
    return {"n": len(test),
            "new": spearman([predict(model, r["x"]) for r in test], his),
            "ai_score": spearman([float(r.get("ai_score") or 0) for r in test], his),
            "judge": spearman([float(r.get("judge_fit") or 0) for r in test], his)}

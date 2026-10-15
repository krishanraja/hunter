"""His house style for letters and CVs, and the stamp that says a package has it.

His words on 2026-10-15, about the Sonos letter hunter had built the night
before: "the cover letters are nowhere near good enough". He rewrote it by hand
and ruled on what every letter and CV must be: one font, his header with the
place and phone that match the role, his link row and banner, one page for the
letter and two at most for the CV, UK spelling for UK and Europe roles, and
every word but the header and the sign-off written for the posting. Until a
package is built that way, his answer was: "pause. No new packages and no Apply
emails until every letter passes the new rules."

So a package goes to him, or to an employer, only when its record carries the
stamp of the current style (FIELD on hunter_seen_roles), and only the composer
that writes his style may write it. The composer in package/build.py writes the
master v4 layout he rewrote, so it writes no stamp, and nothing it built can be
sent: every package on record today reads unstamped, which is true of all of
them. A record from before the column existed reads unstamped too, so the
refusal needs no migration to hold.

Builds wait as well (builds_paused). A package that can never be sent is a
tailor call paid for nothing, and his ruling of the same day was no more model
spend on this work. A build that waits writes nothing to the record, so the role
is chosen again on the first run after the new composer lands.
"""
from __future__ import annotations

# The style his edited Sonos letter set, 2026-10-15. A change to his house
# style is a new value here, and every package stamped with the old one is
# refused until it is rebuilt.
HOUSE_STYLE = "house-2026-10-15"

# Where the record carries it (hunter_seen_roles).
FIELD = "package_style"

# True once the composer that writes HOUSE_STYLE is the one build_one calls
# (docs/HANDOVER_LETTERS.md, L3 and L5). Until then every build waits.
COMPOSER_LIVE = False

# Which builder a live build runs: "house" (letters.py and package/compose.py,
# the only one that writes HOUSE_STYLE) in production. "tailor" is the
# builder before 2026-10-15, kept only until his preview approval so the
# tests written for it still describe it; nothing in production sets it.
BUILDER = "house"

# The outcome a send path counts and the reason it gives, in plain words for
# the note he reads on a Control Center card. Its own outcome: never marked
# against his answer bank, never a missing answer, never a failure.
OLD_STYLE = "built before his house style"
PAUSED = "builds wait for his house style"


def stamped(row: dict | None) -> bool:
    """Whether this record's package was built in his current house style.
    Absent, empty or an older style is no."""
    return bool(row) and (row.get(FIELD) or "") == HOUSE_STYLE


def house_builds() -> bool:
    """Whether build_one runs the house builder."""
    return COMPOSER_LIVE and BUILDER == "house"


def builds_paused() -> bool:
    """Whether a build must wait: no composer here can write his style."""
    return not COMPOSER_LIVE

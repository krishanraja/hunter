"""Values Sheets would turn into something else stay text on Pipeline.

On 2026-10-03 "$225,000" in Comp landed as the number 225000, the append's
read-back refused the batch, and thirteen rows sat half written."""
import pytest

from hunter.sheet import COLS, as_text, make_row, row_diff, validate_row


@pytest.mark.parametrize("value,kept", [
    ("$225,000", "'$225,000"), ("225000", "'225000"), ("300,000", "'300,000"),
    ("2026-10-03", "'2026-10-03"), ("10/3/2026", "'10/3/2026"), ("=1+1", "'=1+1"),
    ("+44 20", "'+44 20"), ("-5", "'-5"), ("12%", "'12%"),
    ("$200,000.00/yr - $250,000.00/yr", "$200,000.00/yr - $250,000.00/yr"),
    ("$225K - $300K", "$225K - $300K"), ("New York, NY", "New York, NY"),
    ("Remote - US", "Remote - US"), ("", "")])
def test_what_sheets_would_parse_is_kept_as_text(value, kept):
    assert as_text(value) == kept


def test_a_single_figure_comp_round_trips_and_passes_the_row_guard():
    row = make_row(company="CLEAR", role="Head of RevOps", jd_url="https://x", score=7,
                   comp="$225,000", location="New York, NY")
    assert row[COLS["Comp"]] == "'$225,000"
    assert not validate_row(row, is_append=True)
    landed = list(row)
    landed[COLS["Comp"]] = "$225,000"         # what Sheets shows for an apostrophe value
    assert not [d for d in row_diff(row, landed) if d[0] == "Comp"]
    landed[COLS["Comp"]] = "225000"            # what it showed without one
    assert [d for d in row_diff(row, landed) if d[0] == "Comp"]

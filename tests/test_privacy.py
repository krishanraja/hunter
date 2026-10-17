"""What of his stays out of the public repository, and still works.

His Section G answers left apply/bankseed.py on 2026-10-15: a re-seed reads
them from system_config, and nothing that prints shows them. Every value here
is invented."""
from hunter.apply import bankseed

SECTION_G = ("Race / ethnicity", "Gender", "Veteran status", "Disability status")


class Cfg:
    def __init__(self, **raw):
        self.raw = raw


def test_the_source_holds_no_section_g_answer():
    assert not [s for s in bankseed.seeds() if s.field_name in SECTION_G]


def test_private_seeds_are_read_from_system_config_and_marked_private():
    cfg = Cfg(**{bankseed.PRIVATE_KEY:
                 '[{"field_name": "Gender", "value": "Invented answer"}]'})
    seeds, note = bankseed.private_seeds(cfg)
    assert [(s.field_name, s.value, s.private) for s in seeds] == [
        ("Gender", "Invented answer", True)]
    assert seeds[0].status == bankseed.SENSITIVE_MARK
    assert note == "1 private seed(s) read from system_config"
    assert "Invented answer" not in note


def test_without_them_those_rows_are_left_alone():
    for cfg in (Cfg(), Cfg(**{bankseed.PRIVATE_KEY: "not json"}),
                Cfg(**{bankseed.PRIVATE_KEY: '[{"value": "no field"}]'})):
        seeds, note = bankseed.private_seeds(cfg)
        assert seeds == [] and "left as they are" in note


def test_a_private_change_never_quotes_its_value_on_a_failed_read_back():
    import pytest
    from hunter.sheet import SheetError
    rows = [["Field", "Value", "Status", "Notes"], ["Gender", "", "", ""]]
    seed = bankseed.Seed("Gender", "Invented answer", private=True)
    changes, _ = bankseed.plan_changes(rows, [seed])
    assert changes and all(c.private for c in changes)

    class S:
        def _write(self, blocks):
            pass

        def read_tab_formulas(self, rng):
            return [["something else"]]
    with pytest.raises(SheetError) as e:
        bankseed.apply_changes(S(), changes)
    assert "Invented answer" not in str(e.value)
    assert "something else" not in str(e.value)


def test_a_private_row_prints_neither_its_name_nor_its_answer():
    # A form's question names the employer that asked it.
    seeds = [bankseed.Seed("Have you ever interviewed at Examplecorp before?", "Invented",
                           private=True, create_if_missing=True),
             bankseed.Seed("Is this a private question too?", "Also invented",
                           private=True)]
    changes, problems = bankseed.plan_changes([["Field", "Value"]], seeds)
    out = "\n".join(bankseed.report_lines(changes, problems))
    assert changes and problems
    for secret in ("Examplecorp", "Invented", "private question", "Also invented"):
        assert secret not in out
    assert "a private row" in out

"""The full run is owed by the slot and the record, never by when cron fires.

Every case here is a day that happened or will: Sunday 27 September, when the
12:00 UTC job started at 15:54, the London-clock guard skipped it and no batch
was sourced; Thursday 1 October, seven hours late; and the October clock change,
which moves the Sunday slot from 12:00 to 13:00 UTC.
"""
import datetime

from hunter import schedule

UTC = datetime.timezone.utc


def at(y, m, d, hh, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=UTC)


def test_the_sunday_slot_is_one_pm_in_london_through_both_clocks():
    # British Summer Time: 13:00 London is 12:00 UTC
    assert schedule.latest_slot(at(2026, 10, 4, 12, 30)) == at(2026, 10, 4, 12)
    # Greenwich Mean Time after 25 October: 13:00 London is 13:00 UTC
    assert schedule.latest_slot(at(2026, 11, 1, 13, 5)) == at(2026, 11, 1, 13)
    # an hour before the winter slot, the owed run is still Thursday's
    assert schedule.latest_slot(at(2026, 11, 1, 12, 30)) == at(2026, 10, 29, 8, 27)


def test_the_thursday_slot_is_eight_twenty_seven_utc():
    assert schedule.latest_slot(at(2026, 10, 1, 9)) == at(2026, 10, 1, 8, 27)
    assert schedule.latest_slot(at(2026, 10, 1, 8, 26)) == at(2026, 9, 27, 12)


def test_a_trigger_four_hours_late_still_runs_the_batch():
    """27 September, replayed: nothing had run since the slot, so the run is
    owed, whatever hour the trigger arrived."""
    d = schedule.decide(at(2026, 9, 27, 15, 54), commands=[], full_runs=[])
    assert d.due and d.slot == at(2026, 9, 27, 12)


def test_a_successful_run_since_the_slot_means_done_whoever_started_it():
    runs = [{"run_at": "2026-10-01T15:41:07+00:00", "status": "success"}]
    d = schedule.decide(at(2026, 10, 1, 17), commands=[], full_runs=runs)
    assert not d.due and "finished" in d.why


def test_a_run_from_before_the_slot_does_not_count():
    runs = [{"run_at": "2026-10-01T15:41:07+00:00", "status": "success"}]
    d = schedule.decide(at(2026, 10, 4, 12, 13), commands=[], full_runs=runs)
    assert d.due


def test_a_run_in_progress_is_not_started_twice():
    """A full run takes about an hour. The next hourly trigger must wait for
    it, not pay for a second sweep of the same market."""
    cmds = [{"state": "running", "requested_at": "2026-10-04T12:13:00Z",
             "started_at": "2026-10-04T12:13:00Z"}]
    d = schedule.decide(at(2026, 10, 4, 13, 13), commands=cmds, full_runs=[])
    assert not d.due and "in progress" in d.why


def test_a_run_killed_mid_way_is_retried_once():
    """A job killed at the workflow timeout leaves its row at 'running'."""
    cmds = [{"state": "running", "requested_at": "2026-10-04T12:13:00Z",
             "started_at": "2026-10-04T12:13:00Z"}]
    d = schedule.decide(at(2026, 10, 4, 16, 13), commands=cmds, full_runs=[])
    assert d.due and "attempt 2" in d.why


def test_two_failures_stop_the_spending_and_say_so():
    cmds = [{"state": "failed", "requested_at": "2026-10-04T12:13:00Z"},
            {"state": "failed", "requested_at": "2026-10-04T13:13:00Z"}]
    d = schedule.decide(at(2026, 10, 4, 14, 13), commands=cmds, full_runs=[])
    assert not d.due and d.stuck


def test_a_failure_for_an_earlier_slot_does_not_block_this_one():
    cmds = [{"state": "failed", "requested_at": "2026-10-01T08:30:00Z"},
            {"state": "failed", "requested_at": "2026-10-01T09:30:00Z"}]
    d = schedule.decide(at(2026, 10, 4, 12, 13), commands=cmds, full_runs=[])
    assert d.due


def test_the_drain_runs_the_owed_batch_and_records_how_it_ended(monkeypatch):
    from hunter import run as run_mod

    inserted, patched, ran = [], [], []

    def fake_get(cfg, table, params):
        if table == run_mod.COMMANDS_TABLE and params.get("state") == "eq.running":
            return [{"id": 7}]
        return []

    monkeypatch.setattr(run_mod, "db_get", fake_get)
    monkeypatch.setattr(run_mod, "db_insert", lambda cfg, t, rows, **kw: inserted.append((t, rows)))
    monkeypatch.setattr(run_mod, "db_patch", lambda cfg, t, k, v: patched.append((t, k, v)))
    monkeypatch.setattr(run_mod, "cmd_run", lambda: ran.append(1) or 0)
    monkeypatch.setattr(run_mod.schedule, "decide",
                        lambda now, c, f: schedule.Decision(True, at(2026, 10, 4, 12), "owed"))

    assert run_mod.scheduled_full_run(object()) == 0
    assert ran == [1]
    assert inserted[0][1][0]["command"] == "run"
    assert patched[-1][1] == {"id": "7"} and patched[-1][2]["state"] == "done"


def test_an_unreadable_record_is_not_permission_to_pay(monkeypatch):
    from hunter import run as run_mod

    def boom(*a, **kw):
        raise RuntimeError("supabase down")

    ran = []
    monkeypatch.setattr(run_mod, "db_get", boom)
    monkeypatch.setattr(run_mod, "cmd_run", lambda: ran.append(1) or 0)
    assert run_mod.scheduled_full_run(object()) is None
    assert ran == []

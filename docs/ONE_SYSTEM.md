# One system: the running record

What moved where in the restructure, and why. Newest entry first. The full
Phase 0 inventory (every command, model job, table writer and surface) is the
private page Krish was sent on 2026-10-08; this file keeps the decisions.

The target, four layers:

1. Scheduled and deterministic, no model calls (GitHub Actions from main).
2. API, unattended, only where volume demands it: the judge.
3. Subscription: a Claude Code routine writes the prose (case, CV and letter,
   essays, door-in openers, cold lookups, newsletter extraction). Every string
   still passes `package/voicegate.py`.
4. Control Center is the one place he acts. The sheet is the record.

---

## 2026-10-08: Phase 0, read only

- Access, checked read only: GitHub push to both repos works; Actions dispatch
  works (`stats` run 314 green); Supabase connector reads work and its deletes
  hang without reaching Postgres, while Supabase's own API completes them;
  the Vercel connector is refused on deployments and Vercel's API works;
  Control Center's three hunter endpoints are deployed and guarded.
- Deleted, with his approval given before this session: the pilot_deals card
  "Stealth Vertical AI Startup". Read back: 0 rows, 11 hunter cards left.
- Found, not yet acted on (each needs his yes):
  - Two credentials still sit in plain text in a synced skill, and the GitHub
    one still authenticates. He revokes them.
  - Control Center's Monday drafter will pick five hunter door-in cards first,
    overwrite their checked opening lines and move them to drafted.
  - Four of the 11 door-in cards pair a company with a person at a different,
    similarly named company (Together AI, Fragment Data Technologies, Physical
    Intelligence, Series Entertainment).
  - `approvals` is never scheduled; the "Apply:" email goes out only by hand.
  - Cross-repo tables with competing writers: pilot_deals, bridge_candidates,
    hunter_application_approvals, workflow_proposals, network_contacts.
  - The spend ledger begins 2026-10-04. In its first four days the judge was
    73 percent of $16.52. Control Center's pilot drafter spends outside it.
- Baseline: 1,483 tests pass offline.

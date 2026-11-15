# 2026-11-15: a posting he sends as a link reaches Pipeline

Status: done, on main. Tests: 2610 passed offline.

- He sent a LinkedIn link: Anthropic, GTM Narrative Lead, New York, $400K to
  $570K. Hunter had never seen it. It is on Pipeline as "sent by Krish", read
  back from the sheet and from hunter_seen_roles, linked to its Greenhouse
  posting (first published 13 November).
- `stage-sent` took only a JSON file, so the scheduled job could not stage a
  link and a session needed the database key. `stage-sent LINK [--company
  NAME] --apply` now reads the link itself (`sentlink.py`): a board link off
  its board; a LinkedIn link from LinkedIn's page, then the employer's board
  for the same title, falling back to LinkedIn's text, recorded checked only
  when LinkedIn says it is open. `stage-sent` is a workflow dispatch option.
- 8 new tests; three deliberate breaks (title match, updated date as posted,
  open check) each failed them.

Next step: when he next sends a link, dispatch `stage-sent` with `LINK
--apply` from the workflow rather than running it locally.

North Star check: moves it. A role at his altitude he found reached Pipeline,
and the next one he sends needs no key in a session.

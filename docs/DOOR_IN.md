# The door in: leaders first, roles second

Status: thesis and proposed plan, written 2026-10-07 at Krish's request.
Nothing in here is built. It is the brief for a future session.

His words, 2026-10-07:

> "my door in to the role I actually want is probably more so getting to know
> a leader that runs a company I want to be a part of and getting them to
> trust me enough to run a mindmake sprint, which turns in to something
> bigger. that is precisely how I built mindmake - amperity offered me the
> chief of staff role after the CEO had met me and liked me."

This repo is public. So this file carries no Mindmake prices, no named
contacts and nothing from a private canon. Where a number lives somewhere
private, the file names the place, not the value.

---

## 1. The thesis in one paragraph

Hunter has been treating the **posting** as the unit of work. The thing that
actually got Krish the seat he wanted was a **leader** who met him, trusted
him, and then made the seat. So the unit should be a pair: a company he would
join, and the one person there who can decide. Hunter already knows which
companies are worth his time. Control Center already knows who he knows and
how warmly. Mindmake already has a paid, 30-day way to prove himself on a real
problem, which is the AI chief of staff job in a box. makeyourmindup already
gives him a reason to talk to a leader that is not a sales pitch. Wired
together, these four become one system. It finds the leaders worth knowing,
earns a warm way in, turns trust into a paid proof, and turns the proof into
continued work, a seat or a stake. Each step pays or teaches on its own, so
the short wins add up to the long one.

---

## 2. Why this is right: the evidence, not the hunch

1. **The one time it worked, it worked this way.** Amperity offered him the
   chief of staff seat after the CEO met him. In hunter's own record, the
   posting route at the same company lost: Amperity's "Head of Partnerships
   and Alliances" scored 8 on merit and he declined it (`src/hunter/gates.py`,
   the `EXCEPTIONAL_MERIT` note). The posting was the wrong seat. The
   relationship produced the right one.
2. **The posting funnel is fighting a ceiling.** His accept rate fell from 77
   to 15 percent in six weeks (CLAUDE.md section 3). Most of his declines are
   about the business, not the seat. A posting is a seat someone else has
   already designed. The seats he wants (AI transformation, chief of staff to
   a founder, a GM who rebuilds the model) are usually designed around a
   person, after the leader has met them.
3. **His own rules point here** (control-center `docs/KRISH.md`, the eight
   v2 decision rules):
   - Rule 2, no cold outbound. A job application is a cold approach to a
     stranger, made through a form. A warm route to a leader is not.
   - Rule 3, cash inside 90 days. A paid proof pays inside 30. A job search
     can run for a year.
   - Rule 4, he stalls when he is alone in selling. A leader who already
     trusts him is the partner who "polices the selling" for one engagement.
   - Rule 5, income without ownership is a job with extra steps. A proof that
     becomes a seat can be negotiated with equity from a position of proven
     value. A posting comes with its band already set.
   - Rule 7, it must put a leader's edge back. This is the mission itself,
     done one leader at a time.
4. **The ikigai already says the job search is parked except for "a role
   that is literally the mission with a salary"** (KRISH.md, Kill / Park).
   The only reliable way to find a role that is literally the mission is to
   do the mission inside the company first.
5. **The seed is already planted.** Hunter's `mindmake_wedge` bridge tier
   (`src/hunter/people/bridges.py`, Krish's idea of 2026-09-15) drafts a
   peer-to-peer note to a leader whose company has a commercial seat open. It
   opens on an observation, never an offer, and makes "put my name in for
   the role" the explicit second road. The comment there predicts it
   "converts to the ROLE more often than to a client". No outcome has ever
   been recorded, so the prediction is untested.

---

## 3. What the system is for, stated as one outcome

**More leaders he wants to work for trusting him enough to pay him for 30
days, at companies he would want to join.** Everything else is a means. A
job offer, a continuation, a stake, a referral to the next leader: those are
the outcomes of that one thing done well and done repeatedly.

The ladder of wins, each of which counts on its own:

| Rung | The win | Pays or teaches | Rule it serves |
|---|---|---|---|
| 1 | A leader he wants to know is named, with a reason and a warm route | teaches: is the list right? | 2, 7 |
| 2 | A real conversation with that leader | teaches: does the observation land? | 2 |
| 3 | A paid 30-day proof on one of their decisions | cash inside 90 days | 3 |
| 4 | Continuation against named deliverables | cash, and depth of trust | 3, 4 |
| 5 | A seat designed around him, or a stake, or both | the destination | 5, 7 |
| 6 | That leader introduces the next leader | the compounding loop | 2, 4 |

Rung 6 is the one that makes this a system rather than a string of lucky
meetings. Every proof should end with a named introduction, asked for in the
last week.

---

## 4. The words: a constraint, not a detail

Mindmake's canon (`krishanraja/mindmake`, `project-documentation/01_CANON.md`)
sets rules this plan must keep, and a few of them collide with the words
Krish used today:

- **"Chief of staff", "fractional" and "sprint" are not public words.** The
  canon lists "chief of staff" on its banned public framing. It lists a
  "generic fractional role or open-ended retainer" under what Mindmake does
  not sell. It retired "the 21-day Sprint" with the old offer ladder. The
  idea Krish described is right. The public vocabulary for it already exists:
  the **paid proof**, entered through **Build your AI brain** (a principal
  buying for themselves) or **Build your AI GTM** (a company). Internally,
  the Brain door is already budget-anchored on "the coaching and
  chief-of-staff line". So the AI chief of staff job is what the proof
  *does*, not what anyone calls it.
- **The proof grows through the existing continuation rule.** Continuation
  is proposed only against named deliverables found in month one. If the
  client continues, the proof is credited as month one of a three-month
  term. That is the ladder from rung 3 to rung 4. Nothing new needs
  inventing.
- **Price stays private.** No hunter output, sheet cell or draft may carry a
  Mindmake price. The figures live in the canon, read by the people who need
  them.
- **One known conflict to resolve.** Control Center's consulting pipeline,
  `pilot_deals`, describes "a paid three week pilot". The canon says a
  30-day proof. One of them is stale. Krish decides which, and the other is
  corrected, before anything new is built on either.

---

## 5. Who owns what: the four repos as one system

Every capability below already exists in some form. The plan connects them;
it adds no new product.

```
  hunter              control-center          mindmake              makeyourmindup
  WHO is worth it     WHO he knows, and       WHAT he offers        WHY a leader
  and WHEN            the pipeline            and delivers          would talk to him

  company score  -->  warm path / ask    -->  brief -> proof   <--  a piece, a
  leader + trigger    pilot_deals ladder      -> continuation       your.call, a
  the observation     weekly asks, outcomes   -> named intro        conversation
        ^                                                                |
        +-------------- outcomes feed back as ground truth --------------+
```

**hunter: finds the pairs and says why now.**
- Already has: the company score (`company.py`, `companyintel.py`), his
  universe and lookalikes (`universe.py`, `lookalike.py`, `radar.py`), his
  Target Companies policy (`targets.py`), new-company discovery
  (`prospect.py`), people and warm paths (`people/ingest.py`, `strength.py`,
  `bridges.py`), the newsletter's talent moves (`sources/newsletter.py`), and
  the judge that has read everything he has written (`judge.py`).
- New job: a **leader card** for each company that clears his bar. It holds
  the company, the decision maker, the trigger (a seat opened, a raise, a
  new leader, a move in the newsletter, a public statement), the best warm
  route, and the one observation he would open on. It reuses the wedge's
  rule that the opener is a diagnosis, never a pitch.
- A trigger matters because a leader is most open to help in the weeks
  after something changes. An open commercial seat is one trigger. It is not
  the only one, and it should not be required.

**control-center: owns the relationship and the pipeline.**
- Already has: one identity per person across sources (`contacts`,
  `contact_identities`), measured warmth from email and meeting metadata
  (`api/_relationshipSync.ts`), what each person can do for him
  (`contact_intelligence.plays`: alumni, multiplier, buyer, amplifier,
  subject), the ask engine (`api/network/ask.ts`), and the consulting ladder
  `pilot_deals`: listed, drafted, sent, replied, call booked, call taken,
  pilot booked, pilot paid, not now. Each state has a timestamp. A trigger
  must carry its source URL or nothing.
- New job: hunter's leader cards land in `pilot_deals` as `listed`, with
  `sourced_by = hunter` and the trigger and its source URL, so there is one
  pipeline, not two. The "weekly three" asks in `docs/NETWORK_STRATEGY.md`
  (planned, not built) include the best door-in asks. Outcomes are written
  back as evidence, as that design already intends.

**mindmake: owns the offer, the brief and the delivery.**
- Already has: the two doors, the paid proof, the continuation rule, the
  company-first brief behind "Start here" (`private.mindmake_brief_requests`,
  `public.mindmake_personal_reads`), and the voice rules.
- New job: none in the product. One addition to the delivery: the last week
  of every proof asks for one named introduction (rung 6), recorded in
  `pilot_deals` as a new `listed` row with `sourced_by = referral`.

**makeyourmindup: earns the right to the first conversation.**
- Already has: the publication (follow the money, under the hood, mind the
  gap) for "a senior leader who will not admit they are not ready", with
  success measured as invitations and quotes, not impressions. It keeps
  Mindmake out of editorial by rule.
- New job: the **subject play**. A leader on a card who has said something
  worth unpicking can be asked for a quote or a short conversation for a
  piece. That is warm by construction: they are asked for their view, not
  sold to. It is also Rule 2's "published thinking" route made specific. The
  editorial rule holds: the piece never mentions Mindmake. The relationship
  is what carries forward.

---

## 6. The rules this must keep

These are inherited, not new. A future session that breaks one has built the
wrong thing.

1. **No cold outbound** (Rule 2). Hunter's `cold_targets` tier finds a named
   person by web search. Under this plan it may be used to *identify* a
   leader. It may never be the route to them. A leader with no warm path, no
   room and no piece is a card that waits, ranked lower, until one exists.
   Never block on no evidence: a card with no route is kept and shown, not
   deleted.
2. **Nothing sends.** Hunter drafts. Control Center drafts. Krish sends.
   (`check-bridges-never-send` in control-center; the `notify.py` guard in
   this repo.)
3. **A choice, not a parallel track.** The wedge's rule stands: never
   approach a leader as a door in while an application to that same company
   is live, and say plainly that the role is the other road.
4. **Observation first, offer never in the opener.** The first message is
   the diagnosis he would give anyway. The offer is what the leader asks
   for.
5. **Every claim is evidenced.** The voice gate (`package/voicegate.py`) and
   the cited-or-silent rule apply to the observation on a card: every number
   and company name traces to a source.
6. **Price is private, and the public words are Mindmake's** (section 4).
7. **Measure before you write the rule** (CLAUDE.md section 2). The first
   phase below is a measurement, not a build.
8. **Targets are his.** No agent sets a numeric target or a stop date
   (KRISH.md). This plan proposes measures and leaves the numbers blank for
   him to fill.

---

## 7. The plan, in phases

Each phase ends at a checkpoint with an observable result. Nothing starts
before the previous phase's checkpoint is met, and nothing in any phase
sends a message on his behalf.

### Phase 0: his decisions (one session, no build)

The questions only Krish can answer are listed in section 9. Without
answers to the first four, the rest is guesswork.

### Phase 1: measure the idea on what already exists (read only)

Use the data already in Supabase. Spend nothing.

- From his Target Companies, his Yes companies and the Company Radar top
  tier, list the companies that clear his bar.
- For each one, check whether hunter or Control Center can name its
  decision maker, and whether there is a warm route: a current or former
  employee he knows, a shared employer (`krish_tenures`), a multiplier
  (`contact_intelligence`), or a recent newsletter move.
- Count what the existing `mindmake_wedge` tier has proposed since
  2026-09-15, and whether any was sent and what happened. Record it, even if
  the answer is zero.
- Write down the Amperity sequence as the first labelled example: how they
  met, how long from meeting to offer, what the leader saw. This becomes the
  first row of the ground truth in Phase 4.

**Checkpoint:** a table of companies against warm-route coverage. The
number that decides the next phase is the share of his top companies with a
named leader AND a warm route. If it is small, the first build is
route-finding (rooms, pieces, multipliers), not cards.

### Phase 2: the leader card (hunter, draft only)

- One card per company that clears his bar and has a named leader. It holds:
  company score and why, the decision maker, the trigger with its source
  URL, the best warm route and its evidence, the observation (one sentence,
  evidenced), and which door it fits (Brain for a principal, GTM for a
  company).
- Cards land in Control Center's `pilot_deals` as `listed`. They do not get a
  new hunter sheet tab unless Krish asks for one. If he does, it follows
  CLAUDE.md section 3c: same columns and look as Pipeline, read from
  Pipeline.
- The judge's corpus already holds his taste in roles. A card needs his
  taste in **leaders and businesses**, which the company score carries. Do
  not build a second judge until Phase 4 shows the score is not enough.

**Checkpoint:** Krish reads ten cards and rules on each: would he want this
person to trust him, yes or no, and why in a few words. That ruling is the
ground truth, like column A.

### Phase 3: the weekly asks (control-center)

- The "weekly three" from `docs/NETWORK_STRATEGY.md` includes the best
  door-in ask: who to ask for the introduction, the ask itself, and why now.
- Every ask records Krish's prediction before it goes and the outcome after.
  That is the pattern `pilot_asks` already uses.
- The `pilot_deals` ladder moves on real events only: a reply, a call, a
  proof booked, a proof paid.

**Checkpoint:** four consecutive weeks where the asks were produced, he
judged them, and the outcomes were recorded. The number of sends is his
choice. The recording is not optional.

### Phase 4: close the loop

- Outcomes feed back the way verdicts do today. Which triggers led to a
  conversation, which routes got a reply, which doors converted, which
  proofs continued.
- A fixture, `tests/fixtures/krish_leaders.json`, holds his leader rulings
  and the real outcomes. It is the sibling of `krish_verdicts.json` and
  `krish_companies.json`. A change that makes its numbers worse is a
  regression.
- The rung 6 introduction from each proof enters as a `listed` card with its
  source.

**Checkpoint:** the funnel numbers in section 8 can be read for any week,
from the record, without asking anyone.

### Phase 5: makeyourmindup's subject play

- Only after Phase 3 shows that warm routes are the bottleneck. A piece is
  slow, and it is the right tool only when no faster warm route exists.
- The card marks a leader whose public thinking is worth unpicking. The
  publication decides whether to ask them, on editorial grounds.

---

## 8. How we will know it is working

These measures can be read from tables that exist or are planned above. The
targets are blank on purpose (section 6, rule 8).

| Stage | Measure | Source | Target (his to set) |
|---|---|---|---|
| Cards | leader cards he rules "yes, I want their trust" | Phase 2 rulings | |
| Routes | share of yes cards with a warm route | `pilot_deals` plus Control Center evidence | |
| Asks | door-in asks sent per week | `pilot_deals.sent` | |
| Conversations | calls taken per ask sent | `call_taken` | |
| Proofs | proofs paid per call taken | `pilot_paid` | |
| Depth | proofs continued past month one | Mindmake delivery record | |
| Destination | seats or stakes offered | recorded by hand, it will be rare | |
| Compounding | introductions per finished proof | `sourced_by = referral` | |

**The kill signal.** Agree it with Krish in Phase 0. A form that fits his
rules: if a named number of warm door-in asks produces no conversation, the
observation or the list is wrong. Fix that before building further. If
conversations produce no proof, the offer framing is wrong, and that is
Mindmake's question, not hunter's.

---

## 9. Questions only Krish can answer

1. **The destination.** At rung 5, which would he take first: a seat, a
   stake, or a retained proof with a stake attached? This decides what a
   proof is steering towards from day one.
2. **The face.** Is the door-in leader the same face as Mindmake's buyer (a
   founder, CEO or revenue owner who can move the decision), or narrower:
   only leaders of companies he would join?
3. **Both roads at once?** When a company has a seat open that he wants,
   does he apply or go to the leader? The wedge's rule says choose. Does
   that hold for every company, or only his top tier?
4. **The words.** Is "AI chief of staff" a private description only, as the
   canon says, or does he want the canon changed? This plan assumes the
   canon stands.
5. **Pilot or proof.** `pilot_deals` says a three week pilot; the canon says
   a 30-day proof. Which is current?
6. **The numbers.** The targets in section 8 and the kill signal.
7. **Capacity.** The canon caps how many proofs can run at once. Do door-in
   proofs count against the same cap as other clients?

---

## 10. The call, and what could make it wrong

**The call.** Make the leader the unit and the paid proof the door. Build
nothing until Phase 1 has measured how many of his top companies have a
named leader and a warm route today. Then build the leader card as a row in
Control Center's existing pipeline, not as a new surface.

**The sharper alternative.** Skip the cards. Take the five companies he
most wants to join, find the warmest route to each leader by hand this week,
and send the wedge's observation through it. Five real outcomes in a
fortnight would teach more than any build. The trade is that it does not
compound, and it depends on his hours for the part he dreads most.

**Next step, 24 to 72 hours.** A read-only session that runs Phase 1 and
answers one question: of his top companies, how many have a named leader
and a warm route right now? It also records the Amperity sequence in his
own words.

**What could make it wrong.**
- **Selling is the activity he avoids most** (KRISH.md, contradiction 2).
  This plan reduces selling to warm conversations, but it does not remove
  it. If the weekly asks are produced and not sent, the system has failed
  in the same place the 25 approaches did. Rule 4 says to name who polices
  the selling. For this plan that is unnamed, and that is the main risk.
- **Amperity may be one lucky meeting, not a method.** One example is not a
  judgement (the company bar's own rule). Phase 4's fixture is what turns it
  into evidence either way.
- **A proof is fee income.** If continuations never become seats or stakes,
  this is consulting by another name, which Rule 5 rejects as the
  destination. Question 1 in section 9 exists to steer each proof towards
  ownership from the start.

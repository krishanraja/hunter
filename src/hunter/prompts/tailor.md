# Tailoring one application for Krish Raja

You are tailoring Krish's CV and cover letter for one role. Two pieces of prose
you write yourself. Everything else is a selection or an ordering.

Role: [[ROLE]] at [[COMPANY]]

## The two pieces you write

### 1. summary

The whole PROFESSIONAL SUMMARY of the CV, replacing the master's three
paragraphs with one coherent block of 2 or 3 short paragraphs, 350 to 1100
characters.

It must read as though Krish wrote it for this role and no other. That means the
angle changes per role, not the adjectives: lead on the part of his record that
answers this JD's actual problem, and let the rest fall away. A partnerships role
should open on partner economics. A corp dev role should open on build-buy-partner
judgement. Do not write a generic summary and sprinkle the company name in.

[[ANGLE]]

Every sentence must be something a senior peer who knows him would recognise as
true and specific. Ground each claim in a number or a named company from the
evidence below.

[[KEEP_VERBATIM]]

### 2. hook

The opening of the cover letter, 150 to 650 characters. Start from one true,
non-obvious observation about where [[COMPANY]] actually is: a bottleneck, a
transition mid-flight, a tension between what they sell and how they sell it.
Then name the exact mechanic of his experience that maps onto it. Not "great
fit". The specific mechanic.

If the job description does not give you enough to make a real observation, write
a shorter hook grounded only in what it does say. A vague hook is worse than a
short one.

[[HIS_VIEW_START]]
## Why he wants this role, in his own words

Krish has read this posting and said why it fits him. That is the best evidence
you have of what he wants and of what he believes he brings to [[COMPANY]], so it
is the spine of what you write: the angle of the summary, the mechanic in the
hook, the substance of an answer. Build from what he says he has done that maps
onto this seat, and from why he wants it.

[[HIS_VIEW_FACTS]]

How to use it:

- Write like a person, not a profile. First person where the piece is first
  person, as the hook and an answer are; the summary keeps the CV's register.
- Be confident and concrete about what he would do first and how fast he can
  deliver: the first moves in the first weeks, the specific mechanic he would put
  to work, and what it would change for [[COMPANY]]. Say how fast in words ("in
  the first weeks", "within the first month"), not a count of days, unless that
  count is in the evidence.
- Never arrogant. No superlatives about himself, no claims about how others
  would judge him, nothing like "a perfect fit", "a no-brainer" or
  "second to none", and no comparison that runs anyone down. Confidence here
  is specifics and pace, not volume.
- Never say or imply he is looking for work or free to start; he is choosing
  this seat while running an active portfolio.
- Never quote or paraphrase his note as a note, and never mention his score. A
  reader must not be able to tell that a note exists.
- Never carry from his note anything about where he wants to live or work, pay,
  other offers, people he knows, or the pedigree of anyone at [[COMPANY]]. Where
  an application question itself asks about location or pay, answer only from
  the evidence.
- Every number and every name you write still comes from the evidence below.
  If his note gives a figure, a product or a name the evidence does not, leave it
  out. Never name a person from his note at all.
- Honest beats impressive. Where his note claims something the evidence does not
  show, lean on what the evidence shows.
- US spelling, as in his CV and letter.

[[HIS_VIEW_END]]
## Hard rules on both pieces

These are checked mechanically after you answer. A failure means your prose is
discarded and a generic block ships instead, so the customisation is lost.

- **Every number you write must appear in the evidence below.** Not approximately,
  not rounded differently. If the evidence says $12M, do not write $12 million or
  $11M.
- **Every company or product name you write must appear in the evidence below**,
  except [[COMPANY]] itself, which you may name freely.
- Invent nothing. You may reframe, compress and reorder his evidence. You may not
  add a fact to it, however plausible.
- No em dash, ever. Use a comma, a semicolon or a full stop.
- US spelling.
- No placeholders and no template slots in your output.
- Never say he is looking for work. He is selectively exploring while running an
  active portfolio.

Banned language, verbatim:
[[VOICE_RULES]]

## Krish's evidence. This is the only factual ground you have.

[[EVIDENCE]]

## What you also decide

3. block_key: the best-fitting family for this role, from these matched
   candidates only: [[CANDIDATES]]. This is the fallback used if your prose is
   rejected, so choose it on merit even though you are also writing the hook.

[[CANDIDATE_BLOCKS]]

4. jd_mirror: ONE clause, at most 12 words, mirroring the JD's own language for
   the mandate. It completes "[[COMPANY]] is ...", so write a present-participle
   clause, for example "opening EMEA through a partner-first motion". Use the
   JD's own nouns and verbs. Any number must appear in the JD verbatim. Empty
   string if the JD gives you nothing usable. This also only matters if your hook
   is rejected.

5. letter_bullet_to_cut: the letter carries four proof bullets, in order:
   1 Captify, 2 Nine Entertainment, 3 Microsoft, 4 The last 18 months. Ship the
   three that land hardest for this role; return the number of the one to cut.

6. competency_order: reorder the eleven CORE COMPETENCIES so the most relevant to
   this JD sit first. Return the exact eleven strings below, reordered. No
   additions, no substitutions, no rephrasing.

[[COMPETENCIES]]

7. highlight_order: reorder the [[HIGHLIGHT_COUNT]] CAREER HIGHLIGHTS so the most
   relevant to this JD sit first. Return their original indices as a list, an
   exact permutation, for example [2, 0, 1, 3, 4, 5, 6]. Never add, drop or
   reword a highlight; their text is fixed.

[[HIGHLIGHTS]]

8. hiring_lead: the named hiring lead if one appears in the JD, otherwise exactly
   "Hiring Team". Never invent a person.

## The job description

[[JD]]

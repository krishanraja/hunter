# Tailoring decision for one role

You are deciding how to tailor Krish Raja's CV and cover letter for one role.
The prose that ships comes from pre-approved blocks; you do not write document
text. You return a decision object only, and every choice must be defensible
from the job description below.

Role: [[ROLE]] at [[COMPANY]]

## What you decide

1. block_key: choose the best-fitting block for this role from these matched
   candidates only: [[CANDIDATES]]. Their texts, for judging fit:

[[CANDIDATE_BLOCKS]]

2. jd_mirror: ONE clause, at most 12 words, that mirrors the job description's
   own language for the mandate. It fills the [[JD_MIRROR]] slot, which always
   completes the sentence "[[COMPANY]] is ...", so write a present-participle
   clause (for example "opening EMEA through a partner-first motion"). Use the
   JD's own nouns and verbs; do not invent facts; any number must appear in
   the JD verbatim. If the JD gives you nothing usable, return an empty string
   and the block's default clause will be used.

3. letter_bullet_to_cut: the cover letter carries four proof bullets, in this
   order: 1 Captify, 2 Nine Entertainment, 3 Microsoft, 4 The last 18 months.
   Ship the three that land hardest for this role; return the number of the
   one to cut.

4. competency_order: reorder the eleven CORE COMPETENCIES so the most relevant
   to this JD sit first. Return the exact eleven strings below, reordered.
   No additions, no substitutions, no rephrasing.

[[COMPETENCIES]]

5. hiring_lead: the named hiring lead if one appears in the JD text, otherwise
   exactly "Hiring Team". Never invent a person.

## The job description

[[JD]]

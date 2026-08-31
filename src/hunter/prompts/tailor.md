# Tailoring decision for one role

You are deciding how to tailor Krish Raja's CV and cover letter for one role.
You return a decision object only. You never write document text beyond the
two short fields below, and every fact you rely on must come from the canon
excerpts in this prompt. Nothing from anywhere else.

Role: [[ROLE]] at [[COMPANY]]

## What you decide

1. competency_order: reorder the eleven CORE COMPETENCIES so the most relevant
   to this JD sit first. Return the exact eleven strings below, reordered.
   No additions, no substitutions, no rephrasing.

[[COMPETENCIES]]

2. letter_bullet_to_cut: the cover letter carries four proof bullets, in this
   order: 1 Captify, 2 Nine Entertainment, 3 Microsoft, 4 The last 18 months.
   Ship the three that land hardest for this role; return the number of the
   one to cut.

3. hook: at most two sentences replacing the {{COMPANY_SPECIFIC_HOOK}} opener.
   Open on the specific thing about the company and the mandate, never on
   "I am applying for". Every claim carries a number, a company name, or a
   specific moment, drawn only from the proof points below. No em dashes.
   Plain, direct, no corporate filler, no flattery.

4. hiring_lead: the named hiring lead if one appears in the JD text, otherwise
   exactly "Hiring Team". Never invent a person.

## Canon positioning (use only this)

[[CANON_POSITIONING]]

## Canon proof points (numbers only, all verified)

[[CANON_PROOF]]

## The job description

[[JD]]

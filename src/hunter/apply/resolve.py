"""Deterministic answer resolution: one harvested form field to one stored answer.

Stage one, here, is pure pattern work and covers the bulk. The harvest on
2026-09-13 across 20 live postings found the same handful of facts asked in many
phrasings: work authorisation alone appeared 17 times in 8 wordings. Collapsing
those onto the Info Bank's 4 authorisation rows is most of the value, and it is
deterministic, so it is testable and it cannot drift.

Stage two, a bounded model classify that may only pick an index out of the
form's own option list, belongs in a later increment. It is deliberately not
here: nothing in this module can author an answer. Anything unmatched comes back
Unanswered, and an Unanswered required field is what forces a manual handoff.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .infobank import AnswerBank, norm_label
from .model import FormField, looks_like_essay, HIS_OWN_KINDS

# Reasons a field is deliberately not auto filled.
NEEDS_KRISH = "needs Krish"
NEEDS_ROLE = "needs the role location"
NEEDS_ESSAY = "needs a drafted essay"
NO_MATCH = "no stored answer matches"


@dataclass(frozen=True)
class Answer:
    value: str
    source: str          # which bank row or profile fact it came from
    flagged: bool = False  # true when Krish must see it before it moves
    note: str = ""


@dataclass(frozen=True)
class Unanswered:
    reason: str
    note: str = ""


Resolution = Answer | Unanswered

YES = "Yes"
NO = "No"


def _has(label: str, *needles: str) -> bool:
    return all(n in label for n in needles)


def _any(label: str, *needles: str) -> bool:
    return any(n in label for n in needles)


# Jurisdiction detection. Order matters: the more specific phrase wins.
def _jurisdiction(label: str) -> str:
    if _any(label, "united states", " us ", " usa", "u s ", "america"):
        return "US"
    if _any(label, "united kingdom", " uk ", "britain", "shoreditch", "london"):
        return "UK"
    if _any(label, "australia",):
        return "AU"
    if _any(label, "canada",):
        return "CA"
    return ""


def _affirmative(value: str) -> str | None:
    """Is a stored answer a yes? Only these words, never a guess."""
    low = (value or "").strip().lower().rstrip(".")
    return low if low in ("yes", "y", "true", "i agree", "agree", "i consent",
                          "consent", "i acknowledge", "acknowledge",
                          "i confirm", "confirm") else None


def _padded(label: str) -> str:
    return " " + norm_label(label).replace("-", " ") + " "


# Krish's residence rule, recorded 2026-09-13: he lives between NYC and London,
# and for any role he applies to he is resident in that role's city. So a
# residence or office question is answerable from the posting's own location.
# These answers are still flagged, so the approval email shows every one before
# anything moves; the rule removes the blocker, it does not remove the gate.
NYC_HINTS = ("new york", "nyc", "brooklyn", "manhattan", "tri state",
             "tri-state", "eastern")
LONDON_HINTS = ("london", "united kingdom", " uk", "shoreditch", "england")
US_HINTS = ("united states", " us", "usa", "america", "san francisco", "sf",
            "remote us", "north america")

# The city, not the neighbourhood. Ashby's geocoder, and every other one this
# layer meets, has no "Brooklyn, New York": typing it offers New York City, and
# typing "Brooklyn" alone offers Brooklyn Park, MINNESOTA. Krish's rule is London
# or New York depending on where the role is, so the answer is the city itself.
BASE_NYC = "New York, United States"
BASE_LONDON = "London, United Kingdom"

# Every Greenhouse board asks for a country as its own required field, separate
# from the city. Answering it with the city string would put "New York, United
# States" into a list of countries and match nothing.
BASE_COUNTRY = {BASE_NYC: "United States", BASE_LONDON: "United Kingdom"}
COUNTRY_HINTS = ("country", "nation")

# Tokens that identify each base inside a question that lists places, so a
# membership question can be answered by testing rather than by assuming.
BASE_TOKENS = {
    BASE_NYC: ("ny", "new york", "brooklyn", "nyc", "united states", "us", "usa"),
    BASE_LONDON: ("uk", "united kingdom", "london", "england", "gb"),
}
# "Do you currently reside in any of the following states: DE, HI, ..." is a
# membership test, not a residence question. Socure asks exactly this, and the
# correct answer for a New York resident is No, because NY is not on their list.
ENUMERATION_HINTS = ("any of the following", "one of the following",
                     "following states", "following countries",
                     "listed below", "any of these")


def role_base(role_location: str) -> str:
    """Which of Krish's two bases this posting resolves to, or empty."""
    low = " " + (role_location or "").lower() + " "
    if any(h in low for h in LONDON_HINTS):
        return BASE_LONDON
    if any(h in low for h in NYC_HINTS):
        return BASE_NYC
    if any(h in low for h in US_HINTS):
        return BASE_NYC
    return ""



def match_option(value: str, options) -> str | None:
    """Map an answer onto one of the form's OWN option labels.

    Vendors rarely offer a bare "Yes" and "No". Harvey's sponsorship question
    offers "No, I do not require sponsorship to work in the country where this
    role is located", and its office question offers four sentences. An answer
    that is not one of the offered labels is not an answer, so every resolution
    for a field with options goes through here before it is returned.
    """
    if not options:
        return value
    want = (value or "").strip().lower()
    labels = [o.label for o in options]
    for label in labels:
        if label.strip().lower() == want:
            return label
    # An enumerated channel list rarely spells the stored answer exactly:
    # ElevenLabs offers "Social media (LinkedIn, Instagram, X etc)".
    if want and want not in ("yes", "no"):
        named = [x for x in labels if want in x.strip().lower()]
        if len(named) == 1:
            return named[0]
    if want in ("yes", "no"):
        # Prefix match on the affirmative or negative sentence, which is how
        # these option lists are actually written.
        hits = [x for x in labels if x.strip().lower().startswith(want)]
        if len(hits) == 1:
            return hits[0]
        if hits and want == "yes":
            return hits[0]
        if hits and want == "no":
            # Several negatives, as in Trulioo's three way sponsorship select.
            # Krish requires no sponsorship now and none in the future, so the
            # option that negates both is the truthful one; a bare negative that
            # concedes a future need is not.
            both = [x for x in hits
                    if "now or in the future" in x.lower()
                    or "or in the future" in x.lower()
                    and "but" not in x.lower()]
            if len(both) == 1:
                return both[0]
            plain = [x for x in hits if "but" not in x.lower()
                     and "future" not in x.lower()]
            if len(plain) == 1:
                return plain[0]
            return None
    return None



# Krish's ruling 2026-09-14: a REQUIRED salary field gets the recorded floor and
# is flagged in the approval email. Info Bank row 37 previously said never enter
# a salary in a form field, which made Fleek and Trulioo impossible to complete;
# the row is amended to required-field-only so the rule and the behaviour agree.
_MONEY = re.compile(r"\$?\s*(\d[\d,]*)\s*([kKmM])?")


def comp_floor(bank: AnswerBank) -> str:
    """The numeric floor, from an explicit numeric row if one exists, otherwise
    parsed from the recorded comp text ("$250K+ base ..." -> "250000")."""
    explicit = bank.value("Salary expectations (numeric floor)")
    if explicit.strip().isdigit():
        return explicit.strip()
    for field_name in ("Salary expectations (verbal answer if asked)",
                       "Target comp"):
        text = bank.value(field_name) or bank.profile.get(norm_label(field_name), "")
        m = _MONEY.search(text or "")
        if not m:
            continue
        amount = int(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").lower()
        if suffix == "k":
            amount *= 1000
        elif suffix == "m":
            amount *= 1_000_000
        if amount >= 1000:
            return str(amount)
    return ""


class Resolver:
    """Resolves against an AnswerBank.

    role_location is the posting's location as the sheet records it. Without it
    every residence and office question stays Unanswered, which is the safe
    default rather than a guess about where Krish is sitting.
    """

    def __init__(self, bank: AnswerBank, role_location: str = ""):
        self.bank = bank
        self.role_location = role_location or ""
        self.base = role_base(self.role_location)

    # ---------- authorisation, the single biggest cluster ----------

    def _authorisation(self, label: str) -> Resolution | None:
        juris = _jurisdiction(label)
        # "eligible to work WITHOUT any visa sponsorship" asks about
        # authorisation, not about needing sponsorship. Both wordings contain
        # "sponsorship", so the negation decides which question it really is.
        negated = _any(label, "without any visa sponsor", "without visa sponsor",
                       "without any sponsor", "without sponsor",
                       "do not require sponsor", "not require any sponsor")
        asks_sponsorship = _any(label, "sponsor") and not negated
        asks_authorised = _any(
            label, "authoriz", "authoris", "eligible", "right to work",
            "legally able", "work permit") or negated
        if not (asks_sponsorship or asks_authorised):
            return None

        # "require sponsorship" is the inverse of "authorised without sponsorship".
        if asks_sponsorship and not _any(label, "if yes", "select the type"):
            field = {"US": "Will you require sponsorship in US?",
                     "UK": "Will you require sponsorship in UK?"}.get(juris)
            entry = self.bank.get(field) if field else None
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
            # Krish's standing position, recorded 2026-09-13: no sponsorship
            # required in US, UK or AU, ever. Absent a jurisdiction, still No.
            return Answer(NO, "Info Bank: sponsorship rows (US and UK both No)")

        if asks_authorised:
            # Trulioo asks "Canada or the USA". Either authorisation satisfies
            # it, and the source must record that the question was a disjunction
            # so a reader of the approval email is not misled.
            if _any(label, "canada") and _jurisdiction(label) == "US" \
                    and _any(label, " or "):
                us = self.bank.get("Authorized to work in US?")
                if us and us.usable:
                    return Answer(us.value,
                                  f"Info Bank: {us.field_name} "
                                  f"(question allows Canada or USA)")
            field = {"US": "Authorized to work in US?",
                     "UK": "Authorized to work in UK?",
                     "AU": "Authorized to work in Australia?"}.get(juris)
            entry = self.bank.get(field) if field else None
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
            if juris == "CA":
                # Trulioo asks "Canada or the USA". US authorisation satisfies it.
                if _any(label, " or "):
                    us = self.bank.get("Authorized to work in US?")
                    if us and us.usable:
                        return Answer(us.value,
                                      "Info Bank: Authorized to work in US? "
                                      "(question allows Canada or USA)")
                return Unanswered(NEEDS_KRISH,
                                  "no stored Canada authorisation")
            if not juris:
                # "the country you currently reside in" / "your intended work
                # location". Both resolve to the residence rule, which is US or UK.
                if _any(label, "reside", "currently living", "intended work",
                        "stated above", "where this role is located",
                        "country where the job is located"):
                    return Answer(YES,
                                  "Info Bank: US, UK and AU authorisation "
                                  "(residence is NYC or London)")
                return Answer(YES, "Info Bank: US, UK and AU authorisation")
        return None

    # ---------- residence, office and relocation ----------

    def _location(self, field: FormField) -> Resolution | None:
        label = _padded(field.label)
        if field.kind != "location" and not _any(
                label, "relocat", "office", "based", "reside", "locat", "country",
                "time zone", "remote", "days per week", "days a week",
                "in person", "primary locations", "zip code", "tri state",
                "commuting distance",
                # The gate and the residence test below disagreed: this list
                # rejected "Where are you currently living (City &
                # State/Province)?" before the residence check, which knows
                # that exact phrase, ever ran. Trulioo asked it and the
                # application was refused for having no answer.
                "currently living", "where are you currently", "current location",
                "anticipated work location", "intended working location"):
            return None
        if _any(label, "zip code", "postcode", "post code"):
            entry = self.bank.get("ZIP")
            if entry and entry.value.strip():
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
            return Unanswered(NEEDS_KRISH, "no stored ZIP")
        residence = _any(label, "currently living", "where are you currently",
                         "current location", "anticipated work location",
                         "intended working location", "primary locations",
                         "which of these three locations")
        willingness = _any(label, "relocat", "office", "in person",
                           "days per week", "days a week", "willing",
                           "comfortable", "based", "reside", "time zone",
                           "commuting distance", "remote")
        if not self.base:
            why = (f"the posting location {self.role_location!r} does not resolve "
                   f"to NYC or London" if self.role_location
                   else "the posting location was not supplied")
            return Unanswered(NEEDS_ROLE, why)
        why = f"residence rule: the role is in {self.role_location}"

        # The country, where that is what was asked. Checked before the
        # enumeration and option paths so a country select is answered with a
        # country rather than with a city or a Yes.
        if _any(label, *COUNTRY_HINTS) and not _any(label, *ENUMERATION_HINTS) \
                and not _any(label, "authoriz", "authoris", "sponsor", "visa",
                             "citizen", "work in the country"):
            return Answer(BASE_COUNTRY[self.base], why, flagged=True)
        # A membership question enumerates places and asks whether he is in one
        # of them. Answer it by testing the list, never by assuming yes.
        if _any(label, *ENUMERATION_HINTS):
            listed = _padded(field.label)
            tokens = BASE_TOKENS[self.base]
            inside = any(f" {t} " in listed for t in tokens)
            return Answer(
                YES if inside else NO,
                f"membership test: {self.base} "
                f"{'appears' if inside else 'does not appear'} in the list this "
                f"question enumerates", flagged=True)
        # A select is answerable only with one of its own options. Never emit
        # free text into a select, and never invent an option it does not offer.
        if field.options:
            for opt in field.options:
                if role_base(opt.label) == self.base:
                    return Answer(opt.label, why, flagged=True)
            # No option names a place, so this is a yes/no style select written
            # as sentences. Answer Yes and let _fit_to_options pick the label.
            return Answer(YES, why, flagged=True)
        if residence or field.kind == "location":
            return Answer(self.base, why, flagged=True)
        if willingness:
            return Answer(YES,
                          f"residence rule: Krish is resident where the role is "
                          f"({self.role_location})", flagged=True)
        return Unanswered(NEEDS_ROLE, "location answer depends on the posting")

    # ---------- the simple one to one fields ----------

    _DIRECT = (
        ("name", ("Full legal name",)),
        ("email", ("Email (job search)",)),
        ("phone", ("Phone",)),
        ("date", ("Earliest start date",)),
    )

    _URL_FIELDS = (
        (("linkedin",), "LinkedIn URL"),
        (("website", "personal site", "blog", "portfolio"), "Personal website"),
        (("github",), "GitHub"),
        (("twitter", " x "), "Twitter / X"),
    )

    def _direct(self, field: FormField) -> Resolution | None:
        label = _padded(field.label)
        if field.kind == "url" or _any(label, "linkedin", "website", "github",
                                      "portfolio", "blog"):
            for needles, bank_field in self._URL_FIELDS:
                if _any(label, *needles):
                    entry = self.bank.get(bank_field)
                    if entry and entry.usable:
                        return Answer(entry.value, f"Info Bank: {entry.field_name}")
                    return Unanswered(NO_MATCH, f"Info Bank {bank_field} is empty")
        if _any(label, "preferred"):
            if _any(label, "last name", "surname", "family name"):
                legal = self.bank.value("Full legal name")
                if legal:
                    return Answer(legal.split()[-1],
                                  "Info Bank: Full legal name (surname)")
            entry = self.bank.get("Preferred name")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, " phone", "phone number", "mobile number"):
            entry = self.bank.get("Phone")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, "legal name"):
            entry = self.bank.get("Full legal name")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        # Greenhouse asks for the given name and the surname as two fields.
        # Both carry kind "name", so the bank's Full legal name went into each
        # and the form read "Krish Raja" twice. Guarded against Ashby's single
        # "Legal First and Last Name", which wants the whole thing and contains
        # the words "last name" inside it.
        if field.kind == "name" and not _any(label, "first and last", "full name"):
            legal = self.bank.value("Full legal name")
            if legal and len(legal.split()) > 1:
                if _any(label, "first name", "given name", "forename"):
                    return Answer(legal.split()[0],
                                  "Info Bank: Full legal name (given name)")
                if _any(label, "last name", "surname", "family name"):
                    return Answer(legal.split()[-1],
                                  "Info Bank: Full legal name (surname)")
        for kind, bank_fields in self._DIRECT:
            if field.kind != kind:
                continue
            for bank_field in bank_fields:
                entry = self.bank.get(bank_field)
                if entry and entry.usable:
                    return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, "pronoun"):
            entry = self.bank.get("Pronouns")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, "current or most recent employer", "current employer"):
            # "Mindmake", one word. 00_NORTH_STAR.md's naming law is absolute and
            # names "Mindmaker" as the variant to never use: a business with two
            # names has none. This said Mindmaker until 2026-09-15, and it was the
            # value going into the employer field of real application forms.
            return Answer("Mindmake", "Profile: current venture")
        if _any(label, "university", "school attended", "degree"):
            entry = self.bank.get("Highest degree completed")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, "how did you hear", "how did you get connected",
                "how did you find"):
            entry = self.bank.get("How did you hear about us (default)")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, "salary", "compensation expectation", "comp expectation"):
            floor = comp_floor(self.bank)
            if field.required and floor:
                return Answer(
                    floor,
                    "Info Bank: the recorded comp floor, entered because this "
                    "field is required (Krish's ruling 2026-09-14)",
                    flagged=True)
            if not floor:
                return Unanswered(NEEDS_KRISH,
                                  "no comp floor is recorded to enter")
            return Unanswered(
                NEEDS_KRISH,
                "this salary field is optional, so it is left to Krish "
                "(Info Bank row 37)")
        if _any(label, "notice period"):
            entry = self.bank.get("Notice period")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        if _any(label, " referred ", " referral ", " referred by",
                " were you referred"):
            entry = self.bank.get("Referrer name + email (if any)")
            if entry and entry.usable:
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
            return Unanswered(NO_MATCH, "no referrer recorded")
        return None

    # ---------- consent and demographics, always flagged ----------

    CONSENT_FIELD = ("Consent to recruiting privacy policies and "
                     "acknowledgements")

    # The same questions, asked as plain text boxes rather than as the kinds
    # hunter keys off. Anaplan asks for Legal First Name, Legal Last Name,
    # Address Line 1, Zip/Postal Code and State/Province as five short_text
    # fields, and the application was refused for having no answer to any of
    # them while every one sat in his Info Bank. Each rule here names the
    # bank row it reads, so a wrong answer can be traced to a wrong row.
    _POSTAL = (
        (("address line 1", "address line1", "street address", "address 1"),
         "Street address"),
        (("zip", "postal code", "postcode", "post code"), "ZIP"),
        (("state/province", "state / province", "state or province",
          " state ", "province"), "State"),
        (("country",), "Country"),
    )

    def _postal(self, field: FormField) -> Resolution | None:
        label = _padded(field.label)
        # "Email address" and "LinkedIn address" are not postal addresses.
        if _any(label, "email", "linkedin", "url", "website", "ip address"):
            return None
        for needles, row in self._POSTAL:
            if _any(label, *needles):
                entry = self.bank.get(row)
                if entry and entry.value.strip():
                    return Answer(entry.value, f"Info Bank: {entry.field_name}")
                return Unanswered(NEEDS_KRISH, f"no stored {row}")
        # City on its own. "City, State" and "City & State/Province" are
        # location questions and _location has already had its turn.
        if _any(label, " city ", "city:") and not _any(label, "state", "province",
                                                       "country", "which city"):
            entry = self.bank.get("City")
            if entry and entry.value.strip():
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        return None

    def _name_parts(self, field: FormField) -> Resolution | None:
        """First and last name asked separately, as text rather than a name."""
        label = _padded(field.label)
        # "Legal First and Last Name" is one box wanting the whole name, and
        # it contains the words "last name". _direct already carves these out
        # for kind "name"; the same carve-out has to hold here.
        if _any(label, "first and last", "full name", "full legal name",
                "first & last"):
            return None
        if not _any(label, "first name", "last name", "surname", "family name",
                    "given name", "forename"):
            return None
        preferred = _any(label, "preferred", "nickname", "goes by")
        legal = self.bank.value("Full legal name") or ""
        parts = [p for p in legal.split() if p]
        if not parts:
            return Unanswered(NEEDS_KRISH, "no stored legal name")
        if _any(label, "last name", "surname", "family name"):
            if len(parts) < 2:
                return Unanswered(NEEDS_KRISH, "the stored legal name has no surname")
            return Answer(" ".join(parts[1:]),
                          "Info Bank: Full legal name (surname)")
        if preferred:
            entry = self.bank.get("Preferred name")
            if entry and entry.value.strip():
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
        return Answer(parts[0], "Info Bank: Full legal name (first name)")

    def _how_did_you_hear(self, field: FormField) -> Resolution | None:
        if not _any(_padded(field.label), "how did you hear", "how did you find",
                    "where did you hear", "how were you referred",
                    "how did you learn about"):
            return None
        entry = self.bank.get("How did you hear about us (default)")
        if entry and entry.value.strip():
            return Answer(entry.value, f"Info Bank: {entry.field_name}")
        return None

    # Two questions every other employer will ask in its own words, where a
    # verbatim Info Bank row would only ever match the one form it came from.
    # Revin asked both and the application was held for them.
    def _verbatim_question(self, field: FormField) -> Resolution | None:
        """An Info Bank row written for this exact question wins outright.

        Phantom's "How did you hear about Phantom?" and Fleek's "What is your
        current Right To Work Status" each have a row holding the exact option
        text the form offers, and each was being answered first by a generic
        rule ("LinkedIn", "Yes") that then matched none of the options and
        blanked. A row he wrote for one question is the most specific answer
        there is and should not be shadowed.

        Only question-shaped labels, though. The bank also holds short field
        names like City, State and ZIP, and letting those win would override
        the residence rule that answers with the ROLE's city rather than his
        own.
        """
        label = (field.label or "").strip()
        if "?" not in label and len(label.split()) < 4:
            return None
        entry = self.bank.get(label)
        if entry and entry.usable and entry.value.strip():
            return Answer(entry.value, f"Info Bank: {entry.field_name}",
                          flagged=bool(entry.always_flagged))
        return None

    def _about_him_now(self, field: FormField) -> Resolution | None:
        label = _padded(field.label)
        # "Previous company" and "company you are applying to" are not this.
        if _any(label, "previous", "last company", "former", "applying to",
                "this company", "our company"):
            return None
        if _any(label, "current company", "current employer", "employer name",
                "where do you currently work", "who do you currently work",
                "present employer", "company you work for",
                "company do you work for", "company are you at"):
            entry = self.bank.get("Current company")
            if entry and entry.value.strip():
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
            return Unanswered(NEEDS_KRISH, "no stored current company")
        if _any(label, "startup experience", "worked at a startup",
                "experience at a startup", "early stage experience",
                "worked in a startup"):
            entry = self.bank.get("Startup experience")
            if entry and entry.value.strip():
                return Answer(entry.value, f"Info Bank: {entry.field_name}")
            return Unanswered(NEEDS_KRISH, "no stored startup experience answer")
        return None

    def _consent(self) -> Resolution:
        entry = self.bank.get(self.CONSENT_FIELD)
        if entry and entry.usable:
            return Answer(entry.value, f"Info Bank: {entry.field_name}",
                          flagged=True)
        return Unanswered(NEEDS_KRISH,
                          "a consent or acknowledgement is Krish's to give, and "
                          f"the Info Bank has no {self.CONSENT_FIELD!r} row yet")

    _DEMOGRAPHIC_FIELDS = (
        (("gender", "sex"), "Gender"),
        (("race", "ethnic", "hispanic"), "Race / ethnicity"),
        (("veteran", "military"), "Veteran status"),
        (("disab",), "Disability status"),
    )

    def _demographic(self, field: FormField) -> Resolution:
        label = _padded(field.label)
        for needles, bank_field in self._DEMOGRAPHIC_FIELDS:
            if _any(label, *needles):
                entry = self.bank.get(bank_field)
                if entry and entry.usable:
                    return Answer(entry.value, f"Info Bank: {entry.field_name}",
                                  flagged=True)
                return Unanswered(
                    NEEDS_KRISH,
                    f"Info Bank {bank_field} is empty; a demographic "
                    f"disclosure is Krish's to give")
        return Unanswered(NEEDS_KRISH, "unrecognised demographic question")

    # ---------- entry point ----------

    def resolve(self, field: FormField) -> Resolution:
        if field.kind in ("file_resume", "file_cover"):
            return Answer("", "package build", note="attached from the built PDF")
        if field.kind == "consent":
            return self._fit_to_options(field, self._consent())
        if field.kind == "demographic":
            return self._fit_to_options(field, self._demographic(field))
        if field.kind in HIS_OWN_KINDS:
            # A repeating sub-form. There is no flat answer for it, and
            # pretending otherwise would put a single string where the form
            # wants several rows. He fills it on the real form, which he is
            # looking at anyway when he presses submit.
            return Unanswered(
                NEEDS_KRISH,
                f"{field.label.strip() or field.kind} is a repeating section "
                f"of the form and is Krish's to complete in the browser")
        if field.kind == "long_text" or (
                field.kind == "short_text" and looks_like_essay(field.label)):
            return Unanswered(NEEDS_ESSAY, "drafted per posting, then approved")

        label = _padded(field.label)
        result: Resolution | None = None
        for attempt in (self._verbatim_question(field),
                        self._authorisation(label), self._location(field),
                        self._name_parts(field), self._postal(field),
                        self._how_did_you_hear(field), self._about_him_now(field),
                        self._direct(field)):
            if attempt is not None:
                result = attempt
                break
        if result is None:
            # Last resort: the Info Bank may hold this question verbatim.
            entry = self.bank.get(field.label)
            result = (Answer(entry.value, f"Info Bank: {entry.field_name}",
                             flagged=bool(entry.always_flagged))
                      if entry and entry.usable else Unanswered(NO_MATCH))
        return self._fit_to_options(field, result)

    def _fit_to_options(self, field: FormField, result: Resolution) -> Resolution:
        """An answer to a select must be one of that select's own labels."""
        if not isinstance(result, Answer) or not field.options:
            return result
        picked = match_option(result.value, field.options)
        # An acknowledgement with exactly one option is a tick box written as a
        # select: "I acknowledge that I have read the Arbitration Agreement" is
        # not a yes/no question, it is the only thing that can be said. His stored
        # Yes does not match the wording, so every such field came back needing
        # him, on nearly every form. Still flagged, so he reads it before
        # approving; a consent is his to give and this only stops him retyping it.
        if picked is None and field.kind == "consent" and len(field.options) == 1 \
                and _affirmative(result.value):
            only = field.options[0].label
            return Answer(only, result.source + " (the form's only option)",
                          flagged=True, note=result.note)
        if picked is None:
            return Unanswered(
                NO_MATCH,
                f"the stored answer {result.value!r} is not one of this form's "
                f"options: {[o.label for o in field.options][:4]}")
        if picked != result.value:
            return Answer(picked, result.source + " (matched to the form's option)",
                          flagged=result.flagged, note=result.note)
        return result

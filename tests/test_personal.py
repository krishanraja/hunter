"""Which roles carry a personal line of his, and which line (package/personal.py).

Measured first on his 138 labelled companies, from each company's own words
about what it does. Every other sentence and every bank entry here is
invented: his own lines live in canon 9.14, never in this repository.
"""
import collections
import json
import pathlib

import pytest

from hunter.package import personal

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "krish_companies.json"


# ---------- measured on his companies ----------

def test_the_categories_place_few_of_his_companies_and_say_how_many(capsys):
    """Measured 2026-10-15. A change to the phrases moves these numbers, and
    must move them knowingly: re-measure and update all three together."""
    rows = json.loads(FIXTURE.read_text())
    by_cat, by_label = collections.Counter(), collections.Counter()
    for r in rows:
        said = [f["value"] for k, f in r["facts"].items() if k == "what_it_does"]
        cats = personal.categories_of(said)
        if cats:
            by_label[r["label"]] += 1
            by_cat.update(cats.keys())
    with capsys.disabled():
        print(f"\npersonal categories: {sum(by_label.values())} of {len(rows)} placed; "
              f"{dict(sorted(by_cat.items()))}; by his label {dict(sorted(by_label.items()))}")
    assert sum(by_label.values()) == 12
    assert dict(by_cat) == {"emerging_media": 8, "voice_audio_speech": 2, "mindset": 2}
    assert dict(by_label) == {"target": 10, "approved": 1, "declined": 1}


# ---------- what places a business ----------

@pytest.mark.parametrize("sentence,cat", [
    ("Velora makes wireless speakers and home audio for people who love music.", "voice_audio_speech"),
    ("Kestrel turns text-to-speech into voices for call centres.", "voice_audio_speech"),
    ("Calmly is a meditation app for busy professionals.", "mindset"),
    ("Shiftwell is building AI coworkers for finance teams.", "future_of_work"),
    ("Tutora runs online courses for adults changing careers.", "personal_development"),
    ("Framelight is a generative video platform.", "emerging_media"),
    ("Northlight is an entertainment company with film and television franchises.", "content_ip"),
    ("Tixwell runs ticketing for live entertainment promoters.", "live_entertainment"),
    ("Pulsebar makes connected fitness equipment and workout content.", "health_fitness"),
    ("Mindloop applies psychology to coaching.", "mindset"),
    ("Sayso builds speech technology for call centres.", "voice_audio_speech"),
])
def test_a_business_that_says_what_it_is_is_placed(sentence, cat):
    assert cat in personal.categories_of([sentence])


@pytest.mark.parametrize("sentence", [
    "Helix develops gene therapy for rare diseases.",
    "Read customer stories from the creators of our platform.",
    "Lumen builds thin film coatings for solar panels.",
    "Your pipeline numbers are music to our ears.",
    "We use machine learning and deep learning to route freight.",
    "Brightfield sells content marketing software to B2B teams.",
    "Quarry is a payroll platform for mid-market companies.",
    "Medline runs healthcare software for health systems and their patients.",
    "Optix values employee stock: the exercise price, vesting and tax.",
    "Lodgely is AI for housing: leasing, touring and maintenance.",
])
def test_a_phrase_inside_something_else_places_nothing(sentence):
    assert personal.categories_of([sentence]) == {}


def test_the_phrase_that_placed_it_is_kept():
    got = personal.categories_of(["Calmly is a meditation app.", "Calmly helps with mindset too."])
    assert got == {"mindset": "meditation"}


# ---------- canon 9.14, written and read ----------

def test_an_entry_written_is_the_entry_read():
    words = "I’ve played in bands for twenty years, and still record at home."
    block = personal.entry_block(1, ["voice_audio_speech"], ["music", "audio"], words, "2026-10-15")
    got = personal.parse_bank("## 9.14 PERSONAL CONNECTIONS.\n\n" + block)
    assert got == [personal.Entry(1, ("voice_audio_speech",), ("music", "audio"), words)]


def test_his_words_are_kept_exactly_even_with_quotes_and_stops_inside():
    words = 'My first job was in a studio. I still say "one more take" too often.'
    block = personal.entry_block(3, ["content_ip", "emerging_media"], [], words, "2026-10-16")
    (e,) = personal.parse_bank(block)
    assert e.words == words and e.terms == () and e.categories == ("content_ip", "emerging_media")


def test_an_entry_without_its_quoted_line_is_not_an_entry():
    text = ("ENTRY 1. Categories: mindset. Terms: . Added 2026-10-15.\n\n"
            "ENTRY 2. Categories: mindset. Terms: . Added 2026-10-15.\n"
            "an unquoted line\n")
    assert personal.parse_bank(text) == []


def test_an_unknown_category_is_dropped_and_an_entry_with_none_left_is_not_read():
    text = ("ENTRY 1. Categories: mindset, astrology. Terms: Calm. Added 2026-10-15.\n\"Words one.\"\n"
            "ENTRY 2. Categories: astrology. Terms: . Added 2026-10-15.\n\"Words two.\"\n")
    (e,) = personal.parse_bank(text)
    assert (e.number, e.categories, e.terms) == (1, ("mindset",), ("calm",))


def test_words_on_two_lines_are_refused_rather_than_split():
    with pytest.raises(ValueError):
        personal.entry_block(1, ["mindset"], [], "line one\nline two", "2026-10-15")


def test_a_term_with_a_full_stop_is_refused_rather_than_cut():
    with pytest.raises(ValueError):
        personal.entry_block(1, ["mindset"], ["calm.com"], "Words.", "2026-10-15")


# ---------- which line a business gets ----------

MUSIC = personal.Entry(1, ("voice_audio_speech",), ("speakers", "music", "audio"),
                       "I have played in bands for twenty years.")
ANY_MEDIA = personal.Entry(2, ("emerging_media",), (), "I made short films before I made decks.")


def test_his_line_goes_to_a_business_that_says_what_it_is_about():
    m = personal.match(["Velora makes wireless speakers and home audio for people who love music."],
                       [MUSIC, ANY_MEDIA])
    assert m.needs_line and m.entries == [MUSIC] and m.missing == []


def test_a_voice_business_without_his_terms_holds_and_asks():
    m = personal.match(["Kestrel builds voice agents for call centres."], [MUSIC])
    assert m.categories == {"voice_audio_speech": "voice agents"}
    assert m.entries == [] and m.missing == ["voice_audio_speech"]


def test_an_entry_with_no_terms_fits_its_whole_category():
    m = personal.match(["Framelight is a generative video platform."], [MUSIC, ANY_MEDIA])
    assert m.entries == [ANY_MEDIA] and m.missing == []


def test_a_business_in_none_of_his_categories_needs_no_line():
    m = personal.match(["Quarry is a payroll platform."], [MUSIC, ANY_MEDIA])
    assert not m.needs_line and m.entries == [] and m.missing == []


def test_a_new_entry_reruns_only_the_roles_it_is_about():
    voice = ["Kestrel builds voice agents and audio tools."]
    media = ["Framelight is a generative video platform."]
    before_v, before_m = personal.match(voice, [MUSIC]), personal.match(media, [MUSIC])
    added = personal.Entry(3, ("voice_audio_speech",), (), "I host a weekly radio show.")
    after_v, after_m = personal.match(voice, [MUSIC, added]), personal.match(media, [MUSIC, added])
    assert before_v.fingerprint() != after_v.fingerprint()
    assert before_m.fingerprint() == after_m.fingerprint()


# ---------- where the sentences come from ----------

def test_the_sentences_are_the_judges_lines_and_the_companys_own():
    answers = {"business": {"quote": "the speaker company for music lovers",
                            "what_it_sells": "Wireless speakers"},
               "function": {"quote": "an entrepreneurial mindset"}}
    got = personal.business_sentences(answers, "Velora makes home audio.")
    assert got == ["the speaker company for music lovers", "Wireless speakers",
                   "Velora makes home audio."]
    assert "mindset" not in personal.categories_of(got)


def test_nothing_recorded_is_no_sentences_and_no_line():
    assert personal.business_sentences(None) == []
    assert personal.business_sentences("not json") == []
    assert personal.business_sentences({"business": "a string"}) == []
    assert not personal.match(personal.business_sentences({}), []).needs_line

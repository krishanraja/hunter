"""The learning loop, pinned by the verdicts that produced it.

Every case below is a real row from the Pipeline and Krish's real words, so
a regression here is a regression against something that already cost him
attention once.
"""
import pytest

from hunter import learn, verdicts
from hunter.sources import company_key, distinctive_tokens, identity_keys


# ---------- the vocabulary ----------

def test_every_dropdown_value_classifies_to_exactly_one_kind():
    for value in verdicts.dropdown_values():
        kind, code = verdicts.parse(value)
        assert kind in ("none", "go", "applied", "rejection"), value
        if value.startswith(verdicts.DECLINE_PREFIX):
            assert kind == "rejection" and code, value


def test_applied_is_an_application_but_already_applied_is_a_miss():
    assert verdicts.parse("Applied") == ("applied", None)
    assert verdicts.parse("Already applied to MongoDB above") == (
        "rejection", "already_applied")


# ---------- reading his own words ----------

@pytest.mark.parametrize("text,code", [
    ("Declined, not interested in Salesforce as a business nor sales "
     "analytics as what I do all day", "business_uninteresting"),
    ("Role doesnt exist but Cursor is a favourite of mine", "dead_posting"),
    ("I dont want to do growth marketing, I would consider if I was a domain "
     "expert but I have no expertise in healthcare", "function_wrong"),
    ("No interest in payroll or working with finance team", "business_uninteresting"),
    ("Not my domain expertise for a GM role", "domain_expertise"),
    ("I dont fit the precise JD requirements", "requirements_mismatch"),
    ("Love this business but I am not based in LATAM or speak native spanish",
     "geo_language"),
])
def test_free_text_verdicts_carry_a_code(text, code):
    assert learn.classify(text)[1] == code


def test_attributes_are_lifted_verbatim_never_invented():
    inf = learn.infer("Declined, not interested in Salesforce as a business "
                      "nor sales analytics as what I do all day")
    assert ("business", "salesforce") in inf.attributes
    assert ("function", "sales analytics") in inf.attributes


def test_an_attribute_is_filed_under_the_code_it_actually_is():
    inf = learn.infer("I dont want to do growth marketing, I would consider if "
                      "I was a domain expert but I have no expertise in healthcare")
    assert ("function", "growth marketing") in inf.attributes
    assert ("domain", "healthcare") in inf.attributes
    events = [{"job_id": "abridge:head-of-growth-marketing", "company": "Abridge",
               "title": "Head of Growth Marketing", "verdict": "rejection",
               "reason_code": "function_wrong",
               "reason_text": "I dont want to do growth marketing, I would "
                              "consider if I was a domain expert but I have no "
                              "expertise in healthcare"}]
    by_id = {learn.rule_id(learn.rule_from_cluster(c)) for c in learn.clusters(events)}
    assert "function_wrong:function:growth marketing" in by_id
    assert "domain_expertise:domain:healthcare" in by_id


def test_no_verdict_produces_no_rule():
    assert learn.clusters([]) == []
    assert learn.clusters([{"verdict": "applied", "reason_code": None,
                            "reason_text": "Applied", "company": "MongoDB"}]) == []


def test_a_code_without_a_named_attribute_needs_two_occurrences():
    one = [{"job_id": "a:b", "company": "Airwallex", "title": "GM",
            "verdict": "rejection", "reason_code": "domain_expertise",
            "reason_text": "Not my domain expertise for a GM role"}]
    assert learn.clusters(one) == []
    two = one + [dict(one[0], job_id="a:c", title="GM North America")]
    assert [c["value"] for c in learn.clusters(two)] == ["airwallex"]


# ---------- the dedupe misses, named for the rows that produced them ----------

def test_writer_enterprise_ai_transformation_lead_east_is_a_duplicate():
    """writer-enterprise-ai-transformation-lead-east:enterprise-ai-transformation-lead-east

    Krish: "Already applied above". The company field carried the role title
    glued onto it, so the identity key never matched the row he had applied
    to and hunter presented the same posting twice."""
    roles = [
        {"job_id": "writer:strategic-ai-transformation-lead-east",
         "company": "Writer", "title": "Strategic AI Transformation Lead (East)",
         "krish_verdict": "Applied"},
        {"job_id": "writer-enterprise-ai-transformation-lead-east:"
                   "enterprise-ai-transformation-lead-east",
         "company": "Writer - Enterprise AI Transformation Lead (East)",
         "title": "Enterprise AI Transformation Lead (East)",
         "krish_verdict": "Already applied above"},
    ]
    events = [{"job_id": roles[1]["job_id"], "company": roles[1]["company"],
               "title": roles[1]["title"], "verdict": "rejection",
               "reason_code": "already_applied",
               "reason_text": "Already applied above"}]
    [f] = learn.system_findings(events, roles)
    assert f["twin"] == "writer:strategic-ai-transformation-lead-east"


def test_mongodb_post_sales_is_not_a_duplicate_but_an_open_application():
    """mongodb-head-of-post-sales-technology:head-of-post-sales-technology

    Krish: "Already applied to MongoDB above". A different role at the same
    company is not a duplicate and must never be suppressed as one; it is
    staged with a note so he is not surprised by it."""
    roles = [
        {"job_id": "mongodb:head-of-ai-platform-gm", "company": "MongoDB",
         "title": "Head of AI Platform GM", "krish_verdict": "Applied",
         "verdict_at": "2026-09-01"},
        {"job_id": "mongodb-head-of-post-sales-technology:head-of-post-sales-technology",
         "company": "MongoDB - Head of Post Sales Technology",
         "title": "Head of Post Sales Technology",
         "krish_verdict": "Already applied to MongoDB above"},
    ]
    events = [{"job_id": roles[1]["job_id"], "company": roles[1]["company"],
               "title": roles[1]["title"], "verdict": "rejection",
               "reason_code": "already_applied",
               "reason_text": "Already applied to MongoDB above"}]
    [f] = learn.system_findings(events, roles)
    assert "twin" not in f
    assert learn.open_applications(roles)["mongodb"]["title"] == "Head of AI Platform GM"


def test_company_names_polluted_with_the_role_still_dedupe():
    assert company_key("MongoDB - Head of Post Sales Technology",
                       "Head of Post Sales Technology") == "mongodb"
    assert company_key("MongoDB", "Head of AI Platform GM") == "mongodb"


def test_a_company_slugged_in_either_order_is_one_company():
    a = set(identity_keys("Cursor (Anysphere)", "Regional Director, Commercial"))
    b = set(identity_keys("Anysphere", "Regional Director, Commercial"))
    assert a & b


def test_a_shared_ai_token_never_merges_two_companies():
    assert not (distinctive_tokens("Scale AI") & distinctive_tokens("Character AI"))


# ---------- approved rules only ----------

def test_nothing_is_suppressed_without_an_approved_rule():
    assert learn.check([], company="Deel", title="General Manager",
                       jd="payroll for global teams") is None


def test_an_approved_rule_suppresses_and_says_which_rule_did_it():
    rule = {"code": "function_wrong", "kind": "function", "value": "growth marketing",
            "scope": "jd_or_title", "action": "drop",
            "job_ids": ["abridge:head-of-growth-marketing"]}
    hit = learn.check([rule], company="Glean", title="VP, Growth Marketing", jd="")
    assert hit and "growth marketing" in hit[1]
    assert learn.rule_id(rule) in hit[1]
    assert learn.check([rule], company="Glean", title="VP, Corporate Development",
                       jd="") is None


def test_the_impact_preview_names_what_a_rule_would_have_cost():
    rule = {"code": "domain_expertise", "kind": "domain", "value": "healthcare",
            "scope": "jd_or_title", "job_ids": ["abridge:head-of-growth-marketing"]}
    roles = [{"company": "Sierra", "title": "Enterprise Sales Director, Healthcare"},
             {"company": "Mutiny", "title": "Head of GTM"}]
    assert learn.impact(rule, roles) == ["Sierra / Enterprise Sales Director, Healthcare"]

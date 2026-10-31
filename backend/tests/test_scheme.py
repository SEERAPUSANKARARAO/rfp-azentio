"""Customer-defined response codes: read from the RFP's own words, never assumed."""

import pytest

from app import enums
from app.agent.formatrules import extract_rules
from app.agent.scheme import (code_for_class, detect_scheme, label_class, needs_for_code, scheme_of,
                              unmapped_codes)

LETTERS = ("Indicate the status of each capability using one of: S = Standard capability, C = Configuration "
           "required, D = Development required, T = Third-party dependency, N = Not available.")


def classes(scheme):
    return {c["code"]: c["klass"] for c in scheme["codes"]}


def test_codes_written_as_letter_equals_label():
    s = detect_scheme(LETTERS)
    assert s["kind"] == enums.SCHEME_CAPABILITY
    assert classes(s) == {"S": "standard", "C": "configuration", "D": "development", "T": "third_party",
                          "N": "not_available"}


def test_a_code_means_what_its_label_says_not_what_its_letter_suggests():
    s = detect_scheme("Use: Y = Standard, P = Configuration, Q = Development, Z = Third Party, X = Not Available.")
    assert classes(s) == {"Y": "standard", "P": "configuration", "Q": "development", "Z": "third_party",
                          "X": "not_available"}
    assert code_for_class(scheme_of({"response_scheme": s}), enums.CAP_NOT) == "X"


def test_codes_given_as_table_rows():
    s = detect_scheme("Response codes\nS Standard capability\nC Configuration required\nD Development required\n"
                      "T Third party dependency\nN Not available\nPlease complete every row.")
    assert classes(s)["T"] == "third_party" and len(s["codes"]) == 5


def test_codes_given_as_a_plain_word_list_are_the_words_themselves():
    s = detect_scheme("Bidders must classify every capability as Standard, Configuration, Development, Third Party, "
                      "or Not Available.")
    assert [c["code"] for c in s["codes"]] == ["Standard", "Configuration", "Development", "Third Party", "Not Available"]
    assert classes(s)["Not Available"] == "not_available"


def test_a_label_that_cannot_be_placed_stays_unmapped_for_the_se_to_decide():
    s = detect_scheme("Use S = Standard, C = Configuration, D = Development, T = Third party, R = Roadmap item.")
    scheme = scheme_of({"response_scheme": s})
    assert unmapped_codes(scheme) == ["R"]
    assert code_for_class(scheme, enums.CAP_NOT) == ""  # the RFP offers no code for it: no code is invented


def test_which_codes_need_an_explanation_estimate_or_attachment():
    s = detect_scheme(LETTERS + " Codes C and D must be accompanied by an explanation. Development items also "
                                "require an effort estimate. Attach a diagram for T.")
    scheme = scheme_of({"response_scheme": s})
    assert needs_for_code(scheme, "S") == []
    assert needs_for_code(scheme, "C") == ["explanation"]
    assert set(needs_for_code(scheme, "D")) == {"explanation", "estimate"}
    assert needs_for_code(scheme, "T") == ["attachment"]


def test_compliance_wording_still_works_and_is_reported_as_a_compliance_scheme():
    rules = extract_rules("State compliance in column E using only: C = Complies, PC = Partially Complies, "
                          "NC = Does Not Comply.")
    assert rules["response_scheme"]["kind"] == enums.SCHEME_COMPLIANCE
    assert {c["code"]: c["klass"] for c in rules["response_scheme"]["codes"]} == {"C": "full", "PC": "partial", "NC": "none"}


def test_the_scheme_is_part_of_the_detected_rules():
    rules = extract_rules(LETTERS + " Responses must not exceed 80 words.")
    assert rules["word_limit"] == 80 and rules["response_scheme"]["kind"] == enums.SCHEME_CAPABILITY


@pytest.mark.parametrize("text", [
    "",
    "Provide ISO (International Organization for Standardization) and UAE (United Arab Emirates) details.",
    "Answer every requirement. Submit by 15 December 2026. Use the supplied template.",
    "We offer three options: gold, silver and bronze.",  # a list, but not capability classes
])
def test_nothing_is_invented_from_text_that_states_no_scheme(text):
    assert detect_scheme(text) == {}


@pytest.mark.parametrize("label,want", [
    ("Standard capability", "standard"), ("Out of the box", "standard"), ("Configuration required", "configuration"),
    ("Development required", "development"), ("Custom development", "development"), ("Third-party dependency", "third_party"),
    ("Partner solution", "third_party"), ("Not available", "not_available"), ("Unsupported", "not_available"),
    ("Roadmap item", ""),
])
def test_label_meaning(label, want):
    assert label_class(label) == want

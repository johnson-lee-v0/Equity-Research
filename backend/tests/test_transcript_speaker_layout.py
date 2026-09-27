"""Speaker layouts seen in NKE's archived call, with synthetic spoken content.

The fixture preserves real label structure, not a dependency on any live source
or on these particular issuer/analyst identities.
"""
from pathlib import Path

import pytest

from backend.app.research.document_intelligence import analyze_transcript, enrich_transcript


FIXTURE = Path(__file__).parent / "fixtures" / "nke_transcript_speaker_layout.txt"
ANALYSTS = ["Adrienne Yih", "Bob Drbul", "Matthew Boss", "Lorraine Hutchinson", "Michael Binetti", "Aneesha Sherman", "Ike Boruchow"]


@pytest.mark.parametrize("mixed_layout", [False, True])
def test_two_line_titles_keep_every_question_and_multiple_management_answers(mixed_layout):
    text = FIXTURE.read_text()
    if mixed_layout:
        # Existing acquisition cleanup normalizes only some titles inline.
        text = text.replace("Elliott Hill\nPresident and CEO, Nike", "Elliott Hill — President and CEO, Nike")
        text = text.replace("Matthew Boss\nAnalyst, JPMorgan", "Matthew Boss — Analyst, JPMorgan")
    result = analyze_transcript(text, "NKE")
    context = result["reading_context"]
    turns = {turn["id"]: turn for turn in context["turns"]}
    exchanges = context["exchanges"]
    assert context["transcript_text"] == text
    assert len(exchanges) == 7
    assert [turns[item["question_turn_ids"][0]]["speaker"] for item in exchanges] == ANALYSTS
    assert [[turns[key]["speaker"] for key in item["answer_turn_ids"]] for item in exchanges] == [
        ["Elliott Hill"], ["Elliott Hill"], ["Elliott Hill", "Matt Friend"],
        ["Elliott Hill", "Matt Friend"], ["Elliott Hill"],
        ["Elliott Hill", "Matt Friend", "Elliott Hill", "Matt Friend"],
        ["Elliott Hill", "Matt Friend"],
    ]
    assert all(item["answered"] for item in exchanges)
    assert {turn["speaker"] for turn in turns.values() if turn["role"] == "management"} == {"Paul Trussell", "Elliott Hill", "Matt Friend"}
    assert {turn["speaker"] for turn in turns.values()} == {*ANALYSTS, "Paul Trussell", "Elliott Hill", "Matt Friend", "Operator"}
    assert any(turn["speaker"] == "Matt Friend" and turn["section"] == "Prepared remarks" for turn in turns.values())
    # Every exact sentence locator points back to its correct original speaker
    # turn; title metadata never becomes spoken evidence or a new person.
    for row in result["sentences"]:
        turn = turns[row["turn_id"]]
        assert row["speaker"] == turn["speaker"]
        assert row["text"] in turn["text"] and row["text"] in text
        assert row["id"] in turn["sentence_ids"]
        assert row["exchange_id"] == turn["exchange_id"]
    assert enrich_transcript(result) == result


def test_same_layout_generalizes_to_different_names_and_issuer():
    text = FIXTURE.read_text().replace("Elliott Hill", "Alex Harper").replace("Matt Friend", "Robin Lee").replace("Paul Trussell", "Casey Jones").replace("Nike", "Other Company")
    for index, name in enumerate(ANALYSTS):
        text = text.replace(name, f"Researcher {chr(65 + index)}son")
    result = analyze_transcript(text, "OTHER")
    assert len(result["reading_context"]["exchanges"]) == 7
    assert {row["name"] for row in result["speakers"] if row["role"] == "management"} == {"Alex Harper", "Robin Lee", "Casey Jones"}


def test_ambiguous_director_stays_unknown_and_actual_executive_is_management():
    text = """Jamie Reed
Managing Director and CEO, Sample Bank
Our company reported strong earnings and improved capital ratios today.
Questions and Answers
Casey Lane — Analyst
Will you maintain the current level of capital investment?
Jamie Reed
We expect to maintain our plans for the coming year.
Morgan Park
Managing Director, Unidentified Organization
The capital investment program includes several different projects.
Jamie Reed
We will publish additional details later this year.
"""
    context = analyze_transcript(text)["reading_context"]
    assert all(turn["role"] == "management" for turn in context["turns"] if turn["speaker"] == "Jamie Reed")
    ambiguous = next(turn for turn in context["turns"] if turn["speaker"] == "Morgan Park")
    assert ambiguous["role"] == "unknown" and ambiguous["exchange_id"] is None
    assert len(context["exchanges"][0]["answer_turn_ids"]) == 1
    assert context["turns"][-1]["exchange_id"] is None


def test_title_only_and_page_metadata_are_not_named_people():
    text = """Earnings Call: Q4 2026
Data Source: Transcript Provider
Managing Director, Brokerage
Senior Managing Director, Another Brokerage
Jamie Reed — CEO
Our quarterly results reflect strong demand and improved margins.
"""
    result = analyze_transcript(text)
    assert {speaker["name"] for speaker in result["speakers"]} == {"Unattributed", "Jamie Reed"}


def test_narrative_mention_does_not_promote_ambiguous_director_to_analyst():
    text = """Jamie Reed — CEO
We received a question from Morgan Park about capital spending.
Morgan Park
Senior Managing Director, Unknown Organization
Our capital spending plans cover several important operating projects.
"""
    context = analyze_transcript(text)["reading_context"]
    assert context["exchanges"] == []
    assert context["turns"][-1]["speaker"] == "Morgan Park"
    assert context["turns"][-1]["role"] == "unknown"


def test_operator_introduction_identifies_questioner_not_other_names_in_sentence():
    text = """Jamie Reed — CEO
We reported our quarterly results earlier today.
Operator
Our next question comes from the line of Taylor Gray with Brokerage. Jamie Reed will respond.
Taylor Gray
Senior Vice President, Brokerage
What are the risks to next year's revenue forecast?
Jamie Reed
We will continue to monitor customer demand and market conditions.
"""
    context = analyze_transcript(text)["reading_context"]
    assert len(context["exchanges"]) == 1
    assert all(turn["role"] == "management" for turn in context["turns"] if turn["speaker"] == "Jamie Reed")
    assert next(turn for turn in context["turns"] if turn["speaker"] == "Taylor Gray")["role"] == "analyst"
    assert context["exchanges"][0]["answered"] is True


@pytest.mark.parametrize("include_long_label", [False, True])
def test_operator_introduction_never_promotes_a_prefix_of_another_person(include_long_label):
    text = """Jamie Reed — CEO
Our quarterly results are available for review today.
Operator
Our next question comes from Jamie Reed Smith with Brokerage.
"""
    if include_long_label:
        text += """Jamie Reed Smith
Managing Director, Brokerage
What are your plans for capital investment next year?
Jamie Reed
We expect to continue investing in our operations.
"""
    context = analyze_transcript(text)["reading_context"]
    assert all(turn["role"] == "management" for turn in context["turns"] if turn["speaker"] == "Jamie Reed")
    if include_long_label:
        assert next(turn for turn in context["turns"] if turn["speaker"] == "Jamie Reed Smith")["role"] == "analyst"
        assert len(context["exchanges"]) == 1
        assert context["exchanges"][0]["answered"] is True
    else:
        assert context["exchanges"] == []


@pytest.mark.parametrize("title", ["Vice President", "Senior Vice President", "President", "EVP", "Managing Director"])
def test_ambiguous_titles_do_not_become_management_answers_without_issuer_evidence(title):
    text = f"""Jamie Reed — CEO, Sample Company
Our company reported its quarterly results earlier today.
Casey Lane — Analyst
What are the risks to your current financial guidance?
Jamie Reed
We are monitoring consumer demand across our markets.
Morgan Park
{title}, Unidentified Organization
Will you reduce spending if sales weaken further?
Jamie Reed
We will consider the available options as conditions change.
"""
    context = analyze_transcript(text)["reading_context"]
    unknown = next(turn for turn in context["turns"] if turn["speaker"] == "Morgan Park")
    assert unknown["role"] == "unknown" and unknown["exchange_id"] is None
    assert len(context["exchanges"][0]["answer_turn_ids"]) == 1
    assert context["turns"][-1]["exchange_id"] is None


@pytest.mark.parametrize("title", ["Vice President and Treasurer", "President and CEO", "Managing Director and CFO"])
def test_explicit_executive_function_identifies_management_with_ambiguous_seniority(title):
    text = f"""Jamie Reed — CEO, Sample Company
Our company reported its quarterly results earlier today.
Morgan Park
{title}, Sample Company
The operating teams have made progress on their plans.
Casey Lane — Analyst
How will you improve efficiency in the next quarter?
Morgan Park
We are investing in new systems and staff training.
"""
    context = analyze_transcript(text)["reading_context"]
    assert all(turn["role"] == "management" for turn in context["turns"] if turn["speaker"] == "Morgan Park")
    assert context["exchanges"][0]["answered"] is True

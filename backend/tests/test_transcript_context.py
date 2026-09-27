"""Conversation context must preserve the evidence behind sentiment filters."""
from copy import deepcopy

from backend.app.research.document_intelligence import (
    TRANSCRIPT_CONTEXT_VERSION,
    analyze_transcript,
    enrich_transcript,
)


CALL = """Jane Doe — CEO
We saw strong demand across the business this quarter.
John Roe — CFO
Our cash generation and liquidity remain strong.
Questions and Answers
Alice Smith — Analyst
Are margins under pressure? Are you seeing weakness in demand?
Operator
Please go ahead with the response.
Jane Doe
We see no weakness in demand. Sales growth remains strong.
John Roe
Our margins improved and costs are stable.
Alice Smith
Thank you.
Alice Smith
And what about the risk from tariffs?
Jane Doe
We are confident in our plans for next year.
Bob Jones — Analyst
Are you forecasting a decline in the next quarter?
"""


def test_negative_question_keeps_positive_answer_and_all_management_speakers():
    result = analyze_transcript(CALL)
    context = result["reading_context"]
    turns = {turn["id"]: turn for turn in context["turns"]}
    question = next(row for row in result["sentences"] if "weakness in demand?" in row["text"])
    assert question["sentiment"]["label"] == "negative"
    assert question["statement_kind"] == "analyst_question"
    exchange = next(item for item in context["exchanges"] if item["id"] == question["exchange_id"])
    assert exchange["answered"] is True
    assert [turns[item]["speaker"] for item in exchange["answer_turn_ids"]] == ["Jane Doe", "John Roe"]
    answer_ids = {row for item in exchange["answer_turn_ids"] for row in turns[item]["sentence_ids"]}
    answers = [row for row in result["sentences"] if row["id"] in answer_ids]
    assert all(row["sentiment"]["label"] == "positive" for row in answers)
    assert all(row["statement_kind"] == "management_answer" for row in answers)
    assert any(turns[item]["role"] == "operator" for item in exchange["context_turn_ids"])
    assert len(exchange["question_turn_ids"]) == 1
    assert "Are margins under pressure? Are you seeing weakness" in turns[exchange["question_turn_ids"][0]]["text"]


def test_followup_creates_new_exchange_and_unanswered_question_stays_unanswered():
    context = analyze_transcript(CALL)["reading_context"]
    turns = {turn["id"]: turn for turn in context["turns"]}
    assert len(context["exchanges"]) == 3
    first, followup, unanswered = context["exchanges"]
    assert first["answered"] and followup["answered"]
    assert "tariffs" in turns[followup["question_turn_ids"][0]]["text"]
    assert len(followup["answer_turn_ids"]) == 1
    assert unanswered["answered"] is False
    assert unanswered["answer_turn_ids"] == []
    assert "next quarter" in turns[unanswered["question_turn_ids"][0]]["text"]
    thanks = next(turn for turn in turns.values() if turn["text"] == "Thank you.")
    assert thanks["id"] in first["context_turn_ids"]


def test_named_thanks_and_acknowledgements_do_not_become_unanswered_questions():
    # Named thanks occur repeatedly in the saved COST call. They should remain
    # visible with the preceding exchange, rather than inflate unanswered Q&A.
    for acknowledgement in ("Thanks, Jane.", "That is helpful. Thanks, Jane.", "Thanks. Appreciate the color."):
        context = analyze_transcript(CALL.replace("Thank you.", acknowledgement))["reading_context"]
        assert len(context["exchanges"]) == 3
        turn = next(turn for turn in context["turns"] if turn["text"] == acknowledgement)
        assert turn["id"] in context["exchanges"][0]["context_turn_ids"]
    # A polite opener followed by a substantive concern remains a question.
    context = analyze_transcript(CALL.replace("Thank you.", "Thanks, Jane. Are you seeing more risk in demand?"))["reading_context"]
    assert len(context["exchanges"]) == 4


def test_complete_transcript_preserves_short_utterances_and_original_text():
    text = """Jane Doe — CEO
Welcome. Our earnings improved during the quarter.
Questions and Answers
Alice Smith — Analyst
Why?
Jane Doe
Sales.
Alice Smith
Thanks.
"""
    result = analyze_transcript(text)
    context = result["reading_context"]
    assert context["version"] == TRANSCRIPT_CONTEXT_VERSION
    assert context["transcript_text"] == text
    assert context["full_text_available"] is True
    question, answer = [turn for turn in context["turns"] if turn["text"] in {"Why?", "Sales."}]
    assert question["sentence_ids"] == answer["sentence_ids"] == []
    assert question["exchange_id"] == answer["exchange_id"]
    assert context["exchanges"][0]["answered"] is True
    assert not any(row["text"] == "Thanks." for row in result["sentences"])
    assert any(turn["text"] == "Thanks." for turn in context["turns"])


def test_unknown_speakers_do_not_invent_management_answer_or_join_new_question():
    text = """Jane Doe — CEO
We reported our results earlier today.
Questions and Answers
Alice Smith — Analyst
Will demand remain weak in the coming months?
Sam Jones: Our sales outlook is uncertain at present.
Mary Blake: Can you explain the capital investment program?
Jane Doe
Our plan is to expand capacity next year.
"""
    result = analyze_transcript(text)
    context = result["reading_context"]
    assert context["exchanges"][0]["answered"] is False
    unknown = [turn for turn in context["turns"] if turn["role"] == "unknown"]
    assert len(unknown) == 2
    assert all(turn["exchange_id"] is None for turn in unknown)
    closing = context["turns"][-1]
    assert closing["statement_kind"] == "management_remark"
    assert closing["exchange_id"] is None


def test_operator_close_does_not_convert_management_closing_into_answer():
    text = """Jane Doe — CEO
We reported our financial results earlier today.
Questions and Answers
Alice Smith — Analyst
What is your current outlook for next quarter?
Operator
There are no further questions today.
Jane Doe
Thank you for joining our call today.
"""
    context = analyze_transcript(text)["reading_context"]
    assert context["exchanges"][0]["answered"] is False
    assert context["turns"][-1]["exchange_id"] is None


def test_backfill_preserves_every_existing_analysis_value_and_does_not_mutate():
    current = analyze_transcript(CALL)
    old = {key: value for key, value in current.items() if key != "reading_context"}
    old["sentences"] = [{key: value for key, value in row.items() if key not in {"turn_id", "exchange_id", "statement_kind"}} for row in old["sentences"]]
    before = deepcopy(old)
    enriched = enrich_transcript(old, CALL)
    assert old == before
    assert enriched == current
    for expected, actual in zip(before["sentences"], enriched["sentences"]):
        assert all(actual[key] == value for key, value in expected.items())
    assert enrich_transcript(enriched) == enriched
    assert all(row["turn_id"] for row in enriched["sentences"])


def test_missing_legacy_raw_input_is_explicitly_incomplete():
    result = analyze_transcript(CALL)
    result.pop("reading_context")
    projection = enrich_transcript(result)["reading_context"]
    assert projection["full_text_available"] is False
    assert "Are margins under pressure?" in projection["transcript_text"]
    assert "Thank you." not in projection["transcript_text"]
    assert len(projection["exchanges"]) == 3


def test_repeated_sentences_map_to_distinct_turns_without_reassigning_ids():
    text = """Jane Doe — CEO
Our outlook remains strong across the business.
Questions and Answers
Alice Smith — Analyst
Is demand weak in the northern region?
Jane Doe
Our outlook remains strong across the business.
Alice Smith
Is demand weak in the southern region?
Jane Doe
Our outlook remains strong across the business.
"""
    result = analyze_transcript(text)
    repeated = [row for row in result["sentences"] if row["text"] == "Our outlook remains strong across the business."]
    assert len({row["turn_id"] for row in repeated}) == 3
    assert [row["statement_kind"] for row in repeated] == ["management_remark", "management_answer", "management_answer"]
    assert repeated[1]["exchange_id"] != repeated[2]["exchange_id"]


def test_html_projection_is_readable_and_retains_short_responses():
    text = "<div>Jane Doe — CEO</div><p>Our quarterly earnings are available for review today.</p><div>Questions and Answers</div><div>Alice Smith — Analyst</div><p>Will you invest in capacity next year?</p><div>Jane Doe</div><p>Yes.</p>"
    context = analyze_transcript(text)["reading_context"]
    assert context["full_text_available"] is True
    assert "<div>" not in context["transcript_text"]
    assert context["turns"][-1]["text"] == "Yes."
    assert context["exchanges"][0]["answered"] is True

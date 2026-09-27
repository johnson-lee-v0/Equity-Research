"""Form-aware comparison fixtures for the automated earnings workflow."""

import pytest

from backend.app.research.document_intelligence import compare_filings


ANNUAL = """FORM 10-K
Table of Contents
Item 1. Business 3
Item 1A. Risk Factors 12
Item 7. Management's Discussion and Analysis 30
Item 8. Financial Statements and Supplementary Data 45
Part I
Item 1. Business
We operate membership warehouses across several international markets.
Item 1A. Risk Factors
We may experience supply disruptions in Europe.
Part II
Item 7. Management's Discussion and Analysis
Revenue increased to $100 million for the year ended August 31, 2025.
We expect customer demand to remain strong.
Liquidity and Capital Resources
Our liquidity remains adequate for our needs.
Item 7A. Quantitative and Qualitative Disclosures About Market Risk
Our exposure to foreign exchange fluctuations remains limited.
Item 8. Financial Statements and Supplementary Data
Consolidated Statements of Income
Revenue 100 200
Notes to Financial Statements
The tax provision was subject to various adjustments.
Item 9A. Controls and Procedures
We identified no material weakness in our internal controls.
Part IV
Item 15. Exhibits and Financial Statement Schedules
This agreement is incorporated herein by reference.
Signatures
We have duly caused this annual report to be signed.
"""

QUARTER = """FORM 10-Q
Part I
Item 1. Financial Statements
Consolidated Statements of Income
Revenue 100 200
The tax provision was subject to various adjustments.
Item 2. Management's Discussion and Analysis
Revenue increased to $200 million for the quarter ended May 31, 2026.
We expect customer demand to remain strong.
Our liquidity remains adequate for our needs.
Item 3. Quantitative and Qualitative Disclosures About Market Risk
Our exposure to foreign exchange fluctuations remains limited.
Item 4. Controls and Procedures
We identified no material weakness in our internal controls.
Part II
Item 1A. Risk Factors
We may experience supply disruptions in Europe.
Item 6. Exhibits
This agreement is incorporated herein by reference.
"""


def test_annual_comparison_retains_risks_mda_and_controls_excludes_item_eight():
    current = ANNUAL.replace("$100 million", "$200 million").replace("August 31, 2025", "August 30, 2026")
    current = current.replace("expect customer demand", "no longer expect customer demand")
    current = current.replace("may experience supply disruptions", "are experiencing material supply disruptions")
    current = current.replace("no material weakness", "a material weakness")
    current = current.replace("tax provision was subject to various adjustments", "tax provision changed following a transaction")
    current = current.replace("incorporated herein by reference", "newly filed with the Commission")
    result = compare_filings(ANNUAL, current)

    assert result["counts"]["changed"] == 3
    assert result["counts"]["numeric_only"] == 1
    assert result["counts"]["added"] == result["counts"]["removed"] == 0
    assert result["documents"]["current"]["form"] == "10-K"
    assert result["documents"]["current"]["missing_core_sections"] == []
    assert result["exclusions"]["current"]["financial_statement_lines"] >= 4
    assert result["exclusions"]["current"]["exhibits_and_signatures"] >= 2
    assert result["exclusions"]["current"]["table_of_contents"] == 4
    assert "tax provision" not in str(result["changes"])
    assert "filed with the Commission" not in str(result["changes"])
    assert any(row["section"].startswith("Part I · Item 1A") for row in result["changes"])
    assert any(row["section"].startswith("Part II · Item 7 ·") for row in result["changes"])
    assert any(row["section"].startswith("Part II · Item 9A") for row in result["changes"])


def test_quarter_comparison_uses_form_metadata_without_cover():
    before = QUARTER.removeprefix("FORM 10-Q\n")
    after = before.replace("adequate for our needs", "insufficient for our needs")
    result = compare_filings(before, after, previous_form="10-Q", current_form="10-Q")
    assert result["counts"]["changed"] == 1
    assert result["coverage"]["matched_sections"] == ["Controls and procedures", "MD&A", "Market risk", "Risk factors"]
    assert result["changes"][0]["section_alignment"] == "matched"
    assert "Item 2" in result["changes"][0]["section"]


def test_equivalent_annual_and_quarter_sections_align_but_scope_is_caveated():
    # Keep the sentences identical so any mismatch must come from item IDs.
    annual = ANNUAL.replace("for the year ended August 31, 2025", "for the quarter ended May 31, 2026")
    quarter = QUARTER.replace("We expect customer demand", "We no longer expect customer demand")
    result = compare_filings(annual, quarter, previous_form="10-K", current_form="10-Q")
    assert result["counts"]["changed"] == 1
    assert result["counts"]["numeric_only"] == 1
    assert result["counts"]["removed"] == 1  # annual-only Business section
    assert result["counts"]["added"] == 0
    assert result["coverage"]["cross_form"] is True
    assert result["coverage"]["previous_only_sections"] == ["Business"]
    change = next(row for row in result["changes"] if row["type"] == "changed")
    assert "Item 7" in change["previous_section"]
    assert "Item 2" in change["current_section"]
    assert change["section_alignment"] == "matched"
    assert any("Annual and quarterly disclosure scope differs" in note for note in result["limitations"])


def test_missing_extracted_risk_section_is_not_silently_treated_as_withdrawn():
    quarter_without_risk = QUARTER.replace("Item 1A. Risk Factors\nWe may experience supply disruptions in Europe.\n", "")
    result = compare_filings(QUARTER, quarter_without_risk)
    assert result["documents"]["current"]["missing_core_sections"] == ["Risk factors"]
    assert result["coverage"]["previous_only_sections"] == ["Risk factors"]
    removal = next(row for row in result["changes"] if row["type"] == "removed")
    assert removal["section_alignment"] == "missing_in_current"
    assert any("cannot establish that the disclosure is absent" in note for note in result["limitations"])


def test_split_table_of_contents_does_not_create_fake_sections_or_headings():
    before = """FORM 10-K
Table of Contents
Item 1A
Risk Factors
12
Item 7
Management's Discussion and Analysis
30
Part I
Item 1A
Risk Factors
We may experience supply disruptions in Europe.
Part II
Item 7
Management's Discussion and Analysis
Customer demand remains strong across our core markets.
"""
    result = compare_filings(before, before.replace("demand remains strong", "demand has weakened"))
    assert result["counts"]["changed"] == 1
    assert result["counts"]["unchanged"] == 1
    assert result["exclusions"]["previous"]["table_of_contents"] == 6
    assert result["documents"]["previous"]["sections"] == ["MD&A", "Risk factors"]


def test_standalone_item_does_not_consume_first_sentence_as_heading():
    before = "Item 7\nWe expect customer demand to remain strong."
    after = before.replace("expect", "no longer expect")
    result = compare_filings(before, after, previous_form="10-K", current_form="10-K")
    assert result["counts"]["changed"] == 1
    assert result["changes"][0]["before"] == "We expect customer demand to remain strong."
    assert result["changes"][0]["section"] == "Part II · Item 7"


def test_annual_references_do_not_change_quarterly_form_detection():
    quarter = QUARTER.replace("We may experience supply disruptions in Europe.", "We may experience supply disruptions as described in our Form 10-K.")
    result = compare_filings(quarter, quarter)
    assert result["documents"]["current"]["form"] == "10-Q"
    assert result["counts"]["changed"] == 0


def test_unsupported_supplied_forms_are_rejected():
    with pytest.raises(ValueError, match="Form 10-K or Form 10-Q"):
        compare_filings(ANNUAL, ANNUAL, previous_form="8-K")


def test_financial_only_annual_filings_have_no_comparable_narrative():
    annual_statements = "FORM 10-K\nPart II\nItem 8. Financial Statements and Supplementary Data\nOur tax provision changed following the transaction."
    with pytest.raises(ValueError, match="No comparable narrative"):
        compare_filings(annual_statements, annual_statements)

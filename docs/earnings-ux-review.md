# Earnings review UX: COST shareholder journey

Reviewed against the saved COST workflow `wf_475a549b06dd4975a6348fbb2469630e` on September 25, 2026. The review uses the existing archive rather than starting another collection or model job. The integrated application was exercised in the in-app browser at 1280 × 900 and 390 × 844.

## The job the page needs to do

“I own COST. What happened in the latest earnings call, which parts challenge my investment thesis, and what did management say in response?”

Success means the user can identify the reporting period and evidence gaps, read meaningful management commentary, explore themes without losing their place, follow a concerning question through the answer, and inspect the full transcript. The page also needs to distinguish business evidence from the separate work needed to assess position size, valuation and portfolio fit.

## Findings in the previous interface

| Friction | Consequence for the shareholder | Change |
| --- | --- | --- |
| The result led with sentence counts and overall lexical tone. | A positive adjective count looked like the answer to whether earnings were good for the investment. | Lead with dated call availability and selected management excerpts paired with explicit thesis-review questions. |
| Agent progress, source collection and three related missing-filing messages came before the call. | The actual reading task began far below operational details. | Keep one short filing-gap notice in the header. Move technical collection detail and source cards into a secondary disclosure after the reader. |
| Saved workflows occupied a sidebar that moved below the long introduction on small screens. | A returning reader could overlook the existing COST call and start again. | Provide a saved-call selector next to intake and restore the newest saved call when no selection is remembered. |
| Every theme expanded into an independent stream of quotes. | Reading one theme displaced the other theme choices and made switching cumbersome. | The integrated reader provides a persistent theme selector and one selected reading view. |
| Negative-sentiment filtering removed adjacent sentences from the other speaker. | An analyst’s question could appear negative while management’s answer vanished. | The integrated reader identifies speaker roles and preserves complete question-and-answer context independently of the language filter. |
| Evidence stopped at the first 150 matched sentences and did not include a full transcript view. | The user could not read the call continuously or verify how the discussion developed. | Include the archived full transcript, searchable speaker turns, highlighted jumps and a transcript download. |
| Portfolio context was a record count at the bottom. | The user could mistake the analysis for an assessment of their actual holding. | Show the most recent retained observation per account and its date, or explicitly state when there is no recorded position. Offer the existing Research Engine handoff as “Review effect on my position.” |

## Content and evidence decisions

The shareholder brief selects exact management excerpts from the saved analysis across demand, margins, outlook, capital and risks. Selection uses topic words, reported metrics and existing theme references; it excludes standard forward-looking-statement boilerplate. Every excerpt links to its highlighted sentence in the full speaker turn. An immediately adjacent qualifying margin statement from the same management speaker is included in full: COST shows both the reported 11-basis-point decline and the 20-basis-point increase excluding gas inflation. This is a reading aid, not a generated earnings verdict. The adjacent questions are questions for reviewing a thesis and are not claims about what the call proves.

The saved COST call contains discussion of fourth-quarter net sales, reported versus adjusted gross margins, outlook, planned capital expenditure and uncertainty around inflation and tariffs. The interface preserves these distinctions through direct quotations and a route to context. It does not turn a “negative” lexical label into an adverse business conclusion or a trading recommendation.

The saved evidence package reports Q4 FY2026 / FY2026, with an earnings date of September 24, 2026. The current 10-K and filing comparison were pending at collection time. This is displayed as a limitation of the saved review, not as a fresh claim about current SEC availability.

The saved COST portfolio snapshot has no positions. The interface therefore says quantity, cost basis and portfolio weight are unknown here. If a future workflow supplies position observations, the brief retains the latest observation per account instead of summing repeated observations, and displays the observation date and status. A deeper review needs to check current holdings and valuation before making a position-level assessment.

## Integrated acceptance walkthrough

Run against the existing saved COST workflow on desktop and a narrow mobile viewport. Do not start a fresh earnings collection or model job merely to inspect the UI.

1. Open Documents → Latest earnings with no remembered selection. Confirm the saved COST review opens automatically and the saved-call selector is visible near intake.
2. Confirm the reporting period, call date, transcript availability and concise filing-comparison limitation can be identified without opening collection metadata.
3. Read the shareholder brief. Confirm excerpts belong to management; the adjacent column is clearly a review question. Confirm no holding, valuation or recommendation is invented.
4. Activate an excerpt’s “Read in context.” Confirm the full transcript opens to the corresponding speaker turn with the source sentence highlighted. Check that returning to findings preserves the prior reading selection.
5. Use “Explore the call & transcript” from the header. Change themes after reading a long theme. Confirm the selector remains reachable and the page does not turn into multiple expanding quote columns.
6. Open negative language. Confirm management and analyst language are distinguished. Select the analyst question about margins and confirm the relevant management reply is visible, including neutral or positive answer text. A filter must never imply that a question alone was management’s negative view.
7. Open the full transcript. Search for a speaker or phrase, clear the search and confirm the full call remains available. Confirm all archived transcript text is present or an honest fallback notice appears for a legacy analysis without raw text. Test download and inspect the downloaded text.
8. Confirm “Your COST position” states no saved holding is known. Confirm the position-review action uses the existing research handoff with archived sources and known evidence gaps; avoid launching paid work during visual QA.
9. Open Sources & workflow details and related research. Confirm the archive and retry controls remain reachable.
10. At a narrow viewport, confirm no horizontal overflow, theme controls and full-transcript controls remain usable, and keyboard focus is visible throughout.

## Verification record

- Source audit: saved workflow API inspected; COST portfolio snapshot is empty; current filing pending; transcript and release archived.
- Implementation: workflow hierarchy and shareholder brief updated; source and prior-research context retained.
- Production frontend build passed. Six reader regression tests passed, covering full-answer retention, grouping, citations beyond sentence 150, exact transcript text and incomplete legacy records.
- The full backend suite passed with 701 tests after context and handoff changes. Four subsequently added download tests passed; the final focused context/download/workflow run passed all 36 tests. The only reported backend warning is an existing Starlette dependency deprecation.
- Desktop walkthrough passed: saved COST review loads; period and filing gap remain visible; paired margin quotes and the unknown holding are explicit; source/progress disclosures open from the section link.
- Themes retain one active selection. Margins page 2 remains selected after opening and returning from an exchange. Keyboard End activates Full transcript from the reader tabs.
- Negative language defaults to management wording. Analyst scope shows four matching sentences in two discussions. Searching `headwind` isolates Christopher Nardone's question and preserves both Gary Millerchip's and Ron Vachris's complete responses (514 words), including reassurance and qualifications. Returning from context and a transcript citation retains the scope and search.
- Full transcript exposes 65 speaker turns over nine pages, including the final operator closing. Speaker search for Ron Vachris returns seven turns. Empty searches show a clear recovery control. Brief citation jumps clear conflicting searches, reveal the exact margin sentence below sticky navigation, then restore the previous search on return.
- Browser QA found and fixed two defects: external citation jumps could scroll before the target rendered; browser-created Blob downloads did not complete in the in-app browser. Citation scrolling now follows the committed view, and saved analyses use an ordinary attachment endpoint.
- The browser downloaded `COST-7292fb6f-transcript.txt`: all 62,310 UTF-8 bytes exactly match the archived input, including short utterances. SHA-256: `4e55f44e3b17e939317214bc75c70a44e2d03fffc18f112d71e85d66c90a1046`.
- Mobile walkthrough passed with a 390-pixel viewport and 375-pixel document width: no horizontal page overflow; theme choices scroll within their own row; all three reader tabs and the answer-return control remain accessible. No browser errors or warnings were reported. The firm's paused state and two queued runs were preserved.
- Local screenshots: `runtime/qa/earnings-reader/after-desktop.jpg` and `runtime/qa/earnings-reader/after-mobile.jpg`. These private QA artifacts are not release assets.

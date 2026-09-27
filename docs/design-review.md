# Research Engine design review

September 25, 2026. Scope: the unified local app, including Documents, Congress,
Strategies, Library, the investment process, and supporting workspaces.

## Method and direction

Applied the published [Impeccable guidance](https://github.com/pbakaus/impeccable)
for critique, technical audit and Operate-mode interfaces. Two independent
source reviews covered design and technical quality; the main task inspected
the running application before implementation and checked the finished screens.
The Impeccable launcher/detector is not installed, so this is a manual review,
not an automated Impeccable score. No detector overlays or automated score are
claimed. Questions were skipped because the user had already authorized the
improvements and the existing research workflows established the product context.

The design serves a returning researcher who needs to scan records, enter
documents, compare results and inspect sources. The existing dark workspace is
retained with quieter surfaces, clear sans-serif type and restrained state
colors. Financial records, research methods and calculations are unchanged.

## Findings and changes

| Finding | Implemented change |
| --- | --- |
| Repeated branding, office labels and status messages crowd the work | One brand/status area, grouped navigation, compact Office/Settings utilities; removed the fixed snapshot footer |
| Thirteen destinations form a long horizontal strip on phones | A labelled Menu control reveals grouped navigation; selecting a page closes it, Escape returns focus, and a skip link reaches content |
| Low-contrast small text and weak input boundaries | Brighter shared text and border tokens, readable research labels/data, visible keyboard focus including links and summaries |
| Slogan headings and decorative framing obscure page purpose | Direct page names, compact heading hierarchy, simpler sections, consistent tabs and quieter metrics |
| Optional document metadata precedes the useful action | Upload/paste comes first; metadata is optional; saved results have Edit source and New analysis actions |
| Dense strategy controls bury the result | Basic setup appears first; execution options are collapsed; the selected result appears before history and receives focus |
| Congress filters fill the narrow screen | Search, chamber and politician remain visible; other filters are grouped with an active count and a clear reset |
| Library search gives no recovery when empty | Explicit no-match guidance and Clear search; flatter finding list and readable source detail |
| Portfolio form overflows on narrow screens | Grid children and form controls can shrink correctly without page overflow |

## Verification

- Production TypeScript/Vite build passed. The pre-existing large Office bundle
  remains lazy-loaded. No backend code changed in this design pass.
- Desktop screenshots inspected Documents, Congress, Strategies, Library,
  Questions and Portfolio. All 13 navigation destinations opened successfully.
- At a 390px emulated viewport (375px content width), Documents, Congress,
  Strategies, Library, Questions and Portfolio had no horizontal **page**
  overflow. Wide record tables retain their own horizontal scrolling.
- Checked expanded mobile navigation, selection/close behavior, Escape focus
  return, the skip link, and document Arrow/Home/End tab switching.
- Confirmed the document textarea computes to 16px on a narrow screen.
- In the isolated QA database, opened a saved analysis, restored its source,
  cleared a new analysis, submitted a transcript and inspected the result.
- Opened a saved backtest: results appear before history, the result region
  receives focus, and ledger controls use labelled button-group semantics.
- Verified Congress search/reset returns all 96,465 records and library search
  displays a recoverable empty state.
- No browser warning/error logs were returned in the final QA session.
- Token contrast calculations found secondary text at least **5.47:1**, control
  borders at least **3.18:1**, and focus indicators at least **7.34:1** against the
  declared shared surfaces. These are scoped measurements, not a claim of a
  complete accessibility certification. See [the technical audit](design-technical-audit.md).

Browser tests used emulated desktop/narrow viewports and keyboard interactions;
physical touch devices and screen readers were not tested. Synthetic analysis
data remains in the separate QA workspace. The main workspace's saved records
and paused processing state are preserved.

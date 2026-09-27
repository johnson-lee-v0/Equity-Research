# Technical design audit — initial state

Assessment B, 25 September 2026. This is an independent source-based audit of the frontend before the design changes. It does not claim rendered browser or assistive-technology verification. No UI files were edited during this assessment.

The requested detector was attempted once:

```sh
.agent/skills/impeccable/scripts/impeccable detect --json frontend/src
```

It returned `no such file or directory: .agent/skills/impeccable/scripts/impeccable`. No detector results are available and no package was installed. The findings below come from inspected JSX/CSS and explicit color calculations.

## Findings

| Priority | Finding and evidence | Suggested correction |
| --- | --- | --- |
| High | **Secondary text is too dim.** `styles.css:5` defines `--faint: #667c70`. On the opaque research card surface `#0c211a`, its contrast is **3.75:1**; on `--panel` it is **3.48:1**. `research.css:98` uses it for 11.52px helper text and `research.css:244` for 10.72px table headings. These are normal text, below the 4.5:1 threshold. | Brighten the secondary text token and verify the least favorable surface; do not rely on increasing letter spacing. |
| High | **Some keyboard focus rings are suppressed by the cascade.** `styles.css:36` provides `input:focus-visible`, but the equally specific, later `.command-input input` (`:87`), `.command-controls input/select` (`:89`) and `.search-input-wrap input` (`:113`) set `outline: 0`. There is no compensating `:focus-within` selector. | Remove the reset or add a later shared focus rule, including links and disclosure summaries. Confirm the composer, selectors and Evidence search with keyboard input. |
| High | **The declared tab widgets are incomplete.** Documents (`Documents.tsx:512`), Congress (`Congress.tsx:224`), Strategies (`Strategies.tsx:410`) and backtest ledgers (`StrategyResults.tsx:316`) have `role="tablist"`, `role="tab"` and `aria-selected`, but no roving `tabIndex`, arrow-key handler, `aria-controls`, or corresponding labelled tab panels. | Build one complete tab component and reuse it, or use ordinary pressed buttons if these are intended as mode switches. |
| Medium | **Fields barely separate from their surfaces.** `research.css:76` uses `--line-strong` for field borders. The token is `rgba(192,216,197,.27)`, which composites to about **2.00:1** against `--panel-deep`, and **2.01:1** against the surrounding raised surface. `--line` is only 1.34–1.38:1. | Introduce a stronger dedicated control border; keep subtle separators for noninteractive divisions. Check each field boundary against the actual adjacent colors. |
| Medium | **Reading text and labels are compressed.** General buttons are 12.16px (`styles.css:92`); research table body is 12.32px, table headings 10.72px, and helper text 11.52px (`research.css:98,239,244`). Several decision metadata/source labels are below 10px (`decision-workspace.css:324,448–456`). | Use a consistent readable scale for working content and data. Reserve small uppercase labels for genuinely secondary information; reduce redundant labels instead of shrinking them. |
| Medium | **Common hit areas are small.** Icon buttons are 30×30px and standard buttons have a 34px minimum height (`styles.css:92`). Table source buttons remove padding entirely (`research.css:265`) and inherit 12.32px text. | Make primary controls and mobile actions comfortably sized, and give inline source actions a minimum hit area without turning every action into a large card. This is a usability finding, not a blanket claim that every target violates a minimum-size rule. |
| Medium | **Small-screen navigation is a long horizontal strip.** At 900px and below, all eight main destinations and five secondary destinations share one nonwrapping scroll row (`styles.css:120`; `App.tsx:99–138,2191–2230`). There is no explicit overflow control or selected-item scrolling; there is also no skip-to-content link before repeated navigation. | Replace the thirteen-item strip with an explicit compact navigation control, preserve the current destination, and add keyboard access to main content. Verify this in a narrow viewport; source inspection alone does not establish an actual clipping failure. |

## Reproducible checks

Relevant source searches:

```sh
rg -n 'outline: 0|focus-within|focus-visible' frontend/src/styles.css
rg -n 'role="tab|aria-controls|tabIndex|onKeyDown' frontend/src/panels/research/*.tsx
rg -n 'font-size:|research-table button|research-field input' frontend/src/panels/research/research.css
```

Contrast used WCAG relative luminance: convert each sRGB channel to linear light, compute `0.2126R + 0.7152G + 0.0722B`, then `(lighter + .05) / (darker + .05)`. Alpha borders were composited over the specified surface before luminance conversion. Opaque token results:

| Foreground | Background | Ratio |
| --- | --- | --- |
| `#667c70` faint | `#071713` background | 4.10:1 |
| `#667c70` faint | `#0c211a` raised | 3.75:1 |
| `#667c70` faint | `#10281f` panel | 3.48:1 |
| `#667c70` faint | `#152e24` soft panel | 3.23:1 |
| `#91a59a` muted | `#0c211a` raised | 6.45:1 |
| `#e6ede6` main text | `#0c211a` raised | 14.12:1 |

Positive evidence to retain: fields in the new research panels have wrapping labels; source tables have horizontal overflow containers; research columns stack at defined breakpoints; the sidebar now scrolls vertically on desktop; reduced-motion handling exists in the global stylesheet; failures use alert/status regions. These reduce risk, but do not substitute for rendered keyboard and responsive testing after the redesign.

## Post-change source review

Reviewed the new `workspace.css`, shared tokens in `styles.css`, application navigation in `App.tsx`, and the document, Congress and strategy source/CSS after the first design implementation. These are source checks; the root task owns rendered browser verification. The detector remains unavailable.

| Initial finding | Source evidence after changes | Status |
| --- | --- | --- |
| Dim secondary text | `--faint` is now `#a0ada7`; its worst ratio on the five declared surfaces is **5.47:1**, against `--panel-soft: #2a3530`. On the main background it is 7.43:1. | Addressed for declared token surfaces. |
| Focus resets | `workspace.css` adds `.app-shell :is(a, button, input, select, textarea, summary, [tabindex]):focus-visible`. Its specificity exceeds the older component `outline: 0` resets. It uses `--green`, with a worst declared surface contrast of **7.34:1**. Main content deliberately suppresses its container outline, while retaining focusability for navigation. | Addressed in inspected source. |
| Incomplete tab widgets | Documents now includes tab IDs, `aria-controls`, active `tabIndex`, Arrow/Home/End handling, and a labelled current panel. Congress and the top-level strategy switch use ordinary pressed buttons in labelled groups. Backtest ledgers and retained legacy mode selectors were also converted to ordinary button groups. | Addressed in inspected source. |
| Weak input boundaries | `--line-strong` is now opaque `#72837b`: **4.42:1** against the input surface and at least **3.18:1** on all declared surfaces. | Addressed for shared research inputs. |
| Compressed type | Research table bodies and inputs are 14px; field labels are 13px; table headings and helpers are 12px. Clearer title and body hierarchy replaces the repeated eyebrow labels. | Improved. Legacy compact evidence labels were not comprehensively restyled. |
| Small targets | Main buttons are at least 38px; mobile buttons/icon controls become 44px, as do mobile table actions. Desktop table actions now have a 32px minimum. | Improved with explicit mobile sizes. |
| Mobile navigation | The horizontal strip is replaced by an in-flow disclosure menu with a native toggle, `aria-expanded`/`aria-controls`, hidden collapsed content, grouped navigation, Escape close and return focus, and focus on main content after selection. A skip link targets the focusable main region. It is not presented as a modal menu, so a focus trap is not required by its chosen interaction model. | Source behavior complete; browser check required. |

The reported document-textarea specificity mismatch was corrected with an explicit 16px rule in its mobile breakpoint. Native file buttons also use a 44px mobile target.

No core control removal was found in the targeted sources. Document upload/paste, all original inputs and optional company metadata, saved source editing, export, history, and research action remain connected. Congress retains search, chamber, politician, action, owner and filing-date filters, CSV export, paging, sources and reported positions. Strategy configuration retains dates, allocation, fees, disclosure delay, sale behavior, repeat buys, the labelled hindsight option and politician selection; advanced controls moved into native details elements.

The root browser audit records runtime evidence separately in `docs/design-review.md`.

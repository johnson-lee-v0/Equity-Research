# How research moves toward a decision

ResearchCouncil runs on your computer. The local Python service keeps the research
history, retrieves configured data, calculates scenarios, and dispatches
model work through the authenticated Codex CLI. Keep the service running for
queued work and Reddit polling to continue.

Research brings together the question, earnings review, answers and decision.
The Reddit origin filter identifies Reddit intake; earnings materials and
company history are subviews. Watchlist, Portfolio, Congress, Strategy testing
and Memory have their own sections. Use the [domain glossary](../CONTEXT.md)
when distinguishing evidence, execution states and investment outcomes.

The Research home page starts with one **Start research** action. Saved case cards
use the identified ticker as their heading, a short decision preview and **Review case**;
the complete original question stays inside the case. Opening a case keeps the complete
rationale, sources and follow-up controls available. Origin and progress filters
still select the same saved cases.

Six public RSS headlines appear immediately below the research input. The local
`GET /api/market-news` reader uses MarketWatch bulletins, Bloomberg Markets and The Wall Street Journal
Markets, with publisher links and publication dates. These RSS feeds are free;
linked full articles follow each publisher’s access terms. It makes no model calls
and uses no key or private research data. Fixed feed URLs, bounded XML responses
and a ten-minute shared cache keep collection small; unavailable feeds retain the
last successful headlines for at most 24 hours with a saved-headlines label.
Malformed, future-dated and more-than-seven-day-old stories are excluded. Empty
feeds show an unavailable state instead of fabricated tiles. The public demo can
use the same response shape from `scripts/export_market_news.py --output PATH`;
its build refreshes that public snapshot without requiring a browser RSS proxy.

Opening a Research or Watchlist card uses the [saved case reader](run-reader.md).
It transfers the displayed claim quotes without repeated full-document excerpts;
complete sources, outputs and historical research remain available on demand.

## Questions, evidence, and follow-up work

The Chief of Staff understands the question or screens a retained Reddit
post. Source discovery finds public materials; the fundamental researcher
assesses their archived content alongside market calculations and relevant
saved research. The CIO challenges the evidence and records the investment
decision. The [task model policy](task-model-routing.md) is the current guide to
model assignments and overrides. Ordinary cases do not require a separate
Portfolio Manager approval chain. Informational questions can receive a direct
answer at intake.

Company investment cases now include an earnings prerequisite after candidate
discovery and before the five-question synthesis. Questions and admitted Reddit
ideas use the same earnings collector as **Research → Earnings & materials**. Each candidate
retains its own package, source versions and coverage notes; the original question
and holding horizon remain unchanged. The visible sequence is **Ticker → Earnings
→ Five questions → Answers → Pricing → Decision**. See
[the investment process](investment-process.md) for responsibilities and related
open-source architectures.

New investment cases use the five-question contract: opportunity, valuation,
catalyst, downside and portfolio action. These questions guide discovery and
synthesis, not just the layout of the final page. The case has a shared budget
of 15 distinct research facts and up to three candidates. Each answer displays
at most two supporting facts and one contradicting fact. Additional observed
inputs needed by valuation still count toward the same budget. Unknowns and
budget limitations remain explicit; the system does not remove an inconvenient
fact to obtain a recommendation.

Discovery accepts at most five initial search queries and six archived pages;
the one evidence continuation accepts one query and three pages. Page limits
are checked before the backend fetches anything. The Codex CLI reports some
search details only after a tool operation completes. The adapter rejects an
unverifiable or over-budget operation, but that check cannot undo work already
performed inside the CLI. These are research acceptance limits, not a guarantee
of a particular subscription token cost.

Laya is a local classifier that participates in the existing workflow. For
Reddit intake it supplies an advisory classification to the Chief of Staff;
it cannot discard a post or authorize research by itself. Before the CIO
assessment, Laya evaluates a compact, selected-evidence packet. Astra receives
that result and explains agreement or a reasoned override. Laya then evaluates
the response against the retained evidence. Unresolved disagreement prevents
a new Recommend outcome and appears as pending review. An unavailable local
model remains an unavailable review rather than a negative investment judgment.

The application records actual classifier execution and its model revision.
Neither Astra's assertion that Laya agreed nor a classifier confidence score
can substitute for that record or for source verification. Laya's short context
limit is checked before inference; an oversized packet is reported explicitly.
No historical case acquires a joint-review label just because the software
was upgraded. See [the contract](five-question-laya-contract.md) for the
question and review fields.

Missing evidence has an owner and a state. One bounded continuation can
collect decision-critical public evidence and update the affected analysis
within the same case. Downloading a source does not itself resolve a gap:
the subsequent assessment must establish whether it answers the question.
Private account inputs, known connector limitations, and future outcomes
receive explicit stopping reasons instead of repeated public searches.
Past attempts remain available, while the current decision has one versioned
representation shared by the result views.
For ordinary single-company five-question cases, follow-up discovery is compared
with the frozen initial analysis. When it adds no new archived evidence or
validated facts, the never-started repeat analysis is explicitly skipped. The
final reviewer uses the intact initial analysis, and the evidence gap stays
unresolved and eligible for the user's targeted retry. A changed document, source
date, validated assertion or invalidated initial analysis prevents that shortcut.
If application code needs to correct a saved decision view, it records a new
revision and its reason. The original model report, source versions, account
snapshot, and earlier decision remain available in history.

## What agent memory means

Reports, decisions, facts, sources, and source versions are stored in SQLite
and the local evidence archive. A model execution receives a selected packet;
it does not retain a private, continuously running conversation between jobs.

Relevant memory can be retrieved for a new question. The task record identifies
the selected records, their source versions, why they were reused, and which
observations were refreshed. A saved opinion is prior analysis, not independent
verification. Factual citations must point to source content in the effective
packet. Superseded or invalidated evidence cannot silently become current
support. Real, demo, and simulation records remain separately scoped.

The saved attempt retains its full explicit source set. The model receives
bounded excerpts that prioritize the current public evidence handoff, current
instrument identity and the market records used by calculations. Omitted
archives remain identified in the packet metadata. Excerpts of supported
dated tables retain recent rows, column headings and original line numbers.
The Memory tab exposes an Obsidian-compatible company vault. Its notes link
back to the ledger; edited notes remain attributed opinions. Research and CIO
attempts receive bounded, source-checked shared memory frozen with the attempt.
Discovery receives only eligible public source leads. See
[shared company memory](shared-company-memory.md) for the current retrieval
contract and [research memory and data access](research-memory-and-data-access.md)
for SEC access and ledger retrieval details.

## Technical analysis and price scenarios

The market connector retrieves available Alpaca price bars with their feed,
timeframe, adjustment policy, and observation time. The bounded packet covers
one-minute, hourly and daily bars, with completed weekly bars derived from the
daily history. Code calculates indicators
from the retained bars before the Researcher interprets them. A short
history or unavailable feed produces an explicit missing value.

The research process runs reproducible Monte Carlo calculations
in local code using retained daily returns. Its activity is identified as a
calculation; it does not claim a model execution. The Researcher and
CIO receive the resulting scenarios. Each result records its source snapshot,
method, calibration window, seed, horizon, assumptions, and result hash.
Quantiles and loss frequencies describe outcomes under those assumptions;
they are not guarantees or independently calibrated forecasts. A scenario
price is not automatically a valuation target or a recommended entry price.
Calculation completion, input quality, and forecast acceptance are separate.
An unresolved price discontinuity prevents the simulated distribution from
being treated as a usable forecast. The market excerpt includes current rows
and compact metadata, while citations keep their original archive line numbers.

The earlier participant simulation remains a separate experiment. It does not
replace the price scenarios attached to an investment question, and its
synthetic records are not promoted into observed market facts.

## The CIO summary

Read the conclusion before opening the detailed flow. Each candidate can be
Recommend, Watchlist, or Decline, with a rationale and horizon. A technical
failure or an unresolved input is an execution state, not a bearish opinion.
Entry and target concepts remain separate:

- **Entry range:** the price conditions that would make an entry reasonable.
- **Target price:** a future value under a stated thesis and horizon.
- **Risks:** what could go wrong if the proposed position is taken.
- **Catalysts:** the developments on which the thesis depends.
- **Invalidation:** evidence or conditions that would break the thesis.
- **Missing inputs:** what still prevents a supported conclusion or allocation.

New decision revisions also require a complete investment argument before
Recommend: a supported thesis, the strongest opposing explanation, an
asset-appropriate valuation, directional payoff, an entry and exit rule,
measurable invalidation, and a dated catalyst or thesis review. An incomplete
argument stays pending or retains an explicit observable Watchlist condition.
Code recalculates the gate; a model cannot approve its own inputs.

Ordinary single-company five-question cases prepare valuation before the final
review. Code validates the analyst's retained assumptions and calculates the
conditional prices first; valid inputs require no additional model call. Invalid
structured inputs permit at most one compact correction with a 120-second limit
for the same frozen evidence, model and calculation rules. Saved corrections
survive a restart; an interrupted or failed correction does not reset its budget.
An unavailable financial baseline remains unpriced. The final reviewer explains
the prepared calculation and can accept it, disagree with its assumptions, or
reject the method with a reason. It cannot silently replace the inputs or the
requested holding horizon. Final source and calculation checks still apply.
Correction usage is retained separately and included in newly frozen research
effort totals when the provider reports it; unreported usage remains unknown.

Valuation methods include earnings multiples, discounted cash flow, an
enterprise-to-equity bridge, and asset/NAV scenarios where company earnings
are inapplicable. One suitable supported method is sufficient. Historical
facts must match their issuer, metric, currency, scale, period and accounting
basis in archived evidence. Forecast inputs remain labeled assumptions, even
when the historical baseline is supported. Bear/base/bull values are
conditional scenarios, without invented probabilities. Unknown fees,
dividends, financing or borrow costs leave net returns unavailable.

The comparison includes retaining cash and the selected benchmark. A cash
projection requires the original dated account terms, matching currency,
explicit rate convention and day basis, and a supported horizon. It uses the
proposed allocation rather than maximum capacity. Short exposure or sale
proceeds are not treated as cash capital. Missing terms remain unavailable;
a future benchmark return is not inferred from historical performance.

Prices stay absent when the evidence does not support them. A missing sizing
input does not erase a supported watch price or catalyst. Share counts come
from code using the relevant account, currency, budget, exposure and risk
inputs. Maximum permitted shares and a proposed allocation are separate: a
policy ceiling does not say the whole allowance should be invested. Only a
positive proposed allocation with a rationale and passing constraints can be
presented as the recommendation. Candidate alternatives each show an independent position preview;
their share counts must not be added together. An explicitly combined
allocation shares the cash budget and remaining position slots. Proposed
limits remain visibly provisional. Cash in another account is a potential
funding source until a transfer is confirmed, and TFSA constraints remain
separate from ordinary trading accounts. ResearchCouncil does not send orders or move
money.

The expandable flow shows the input claim, collected evidence, assessment,
calculations, and final decision. It retains concise rationale, source links,
unknowns and the stage where work stopped. A source matching a quotation does
not prove the source's claim is true. Reddit claims and model opinions are
kept separate from corroborated facts and explicit scenario assumptions.

## Retained documents and paper review

Readable digital PDFs are extracted with page and line provenance and their
original bytes retained. The source archive preserves extraction warnings and
failures for corrupt, encrypted or image-only documents. OCR is not implied,
and a table's visual alignment does not by itself establish which metric,
period or unit a number describes. An updated page is a new evidence version;
retrieving an old document again does not make its observations current.

Each new candidate decision freezes a paper baseline, including its revision,
original outcome, reference close, benchmark choice where supplied, and
measured research usage. A close must have been retained and available before
the decision. A retained asset ID and exchange must match the later observations
for both the instrument and benchmark. Missing starting prices or identity cannot
be filled in later with a more convenient entry. Historical code corrections are marked retrospective and
excluded from prospective comparisons.

The existing local watch loop can refresh bounded daily-price observations
when monitoring and market data are enabled. Firm pause stops this work.
Opening a case or the journal never fetches data or writes a new result.
Comparisons include watched and declined ideas, use the first decision per
case/candidate in aggregate counts, and report unavailable observations.
Benchmark returns require matching dates and currency. Results are
hypothetical price returns, excluding dividends, fees, borrowing and FX;
they are not trading performance. Thesis and catalyst outcomes require a
separate evidence-backed review and never follow automatically from price.

Lifecycle tracking includes recommended, watched, explicitly held, and
reopenable declined ideas. A recommendation never becomes an actual holding
without user confirmation. Dated reviews remain visible while paused. Each
admitted watch event retains its own review episode; an unchanged old date
does not repeatedly launch research. Price conditions rearm only after a later
observation clears the condition and another later observation meets it again.
Failed reviews remain visible for explicit attention.
Ordinary price checks retain their sources even when no condition fires. A failed
refresh preserves the last dated quote and marks the check unavailable. Pausing
during a check stops subsequent network requests.

## Reddit intake

The Reddit inbox tracks accessible r/wallstreetbets submissions, their retrieval
state, and their progress into research. Posts are leads and sentiment evidence;
they cannot independently verify a company's financial claims. Post text is
untrusted source material and cannot change application settings or agent rules.

The initial window is the past seven days, followed by a poll for new posts
each minute while enabled and the local service runs. Up to three Reddit cases
can run concurrently, and active user questions take priority. The initial history window
and later polling share durable identities so
repeated retrieval does not create duplicate intake. Backlog and processing
states remain visible while research is bounded to local capacity. Historical
coverage is reported separately from successful polling: Reddit listing limits,
deleted posts, access restrictions, or downtime can leave gaps.

Every root Reddit submission first receives an A00 Chief of Staff screen using
the retained title, body, and flair for that post. Specialist research proceeds
only for a post-level investment thesis with supporting reason/evidence/catalyst
or for a YOLO post with an identifiable ticker or named issuer. A named issuer
must be resolved through public evidence before assigning a ticker. A YOLO lead is routed
through the ordinary public discovery, market, scenario, and CIO workflow;
it is a lead rather than a recommendation. Memes, screenshots, daily threads,
bare ticker hype/news, and unidentified image-only symbols are skipped
with a visible reason. The backend binds the screening excerpt and tickers to
the exact retained source version and fails closed when the decision is missing
or cannot be validated. Source edits clear the decision for a fresh screen;
score-only observations preserve it.

An explicit Reddit submission URL uses the same screening workflow. Reading
one selected post does not enable the monitor or advance its historical
coverage checkpoint. Empty text and unread image content remain visible
limitations; the system does not invent the author's position or thesis.

Credentials are loaded only by the backend from its private configuration or
environment. The source distribution contains no credentials or personal
research archive. Database/evidence backups do not replace a separate secure
copy of your connector credentials.

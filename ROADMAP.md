# Cohorte roadmap: from skill to category leader

The path from today's open-source skill to a company worth $1B. Each version ships something usable, and each
one has a **proof gate**: the version isn't done until the gate is passed with numbers we can publish.
Revenue and valuation figures are targets for planning, not forecasts.

## Where we are (v0.2, today)

| Proven | Not proven |
|---|---|
| Full MiroFish-style pipeline runs end to end (16 tests, Python 3.9–3.13) | **Whether simulations predict real reactions** (never compared with real outcomes) |
| Specific, in-character agent behaviour; objections emerge without being scripted | Stability: noise between two runs of the same scenario was never measured |
| 10,000 agents for $0.13 / 2.5 min (3 simulated hours, Haiku 5.5) | Crowd numbers come from uncalibrated constants and look more precise than they are |
| Resumable, deterministic, forkable runs | The full skill (Claude writes the world *and* the report) was never run on a real user project |
| | Claude API backend, long runs (48 h+) and rate limits at scale are untested |

**Since then (v0.3, in progress):**
- `ingest` and `assess` rate any subject kind (apps, websites, videos, films, articles, products, pitches, events…);
- a confidence index combines stability across runs, completeness of material, agreement between models and
  coverage;
- `calibrate` learns from real outcomes and is the only thing that lifts the 60/100 cap.

Ensembles and calibration exist; what's still missing is the real outcome data.

**The core risk:** if the robots can't beat a cheap baseline at anticipating real reactions, the product is a
toy. The roadmap therefore puts proof first and polish later.

## Overview

| Version | Theme | Who uses it | Proof gate | Business milestone |
|---|---|---|---|---|
| **v0.3** | Trustworthy runs | You and early adopters | Results stable across seeds; a planted A/B difference is detected | Public demo reports |
| **V1** | Proven | Open-source users, 3–5 design partners | Beats baselines on 20+ historical cases, with a published scorecard | First letters of intent |
| **V2** | Grounded in *your* customers | 10–20 design partners | Grounding with real data measurably improves accuracy | First paid pilots |
| **V3** | Hosted product | Product, marketing and comms teams | 40%+ of pilots convert to paid; repeat use each month | ~$1M ARR |
| **V4** | Platform and learning loop | Mid-market and enterprise | Accuracy improves release after release from outcome data | ~$10M ARR |
| **V5** | System of record for decisions | Enterprises, agencies, public sector | Customers make go/no-go decisions on Cohorte forecasts | $50–100M ARR → $1B+ |

---

## v0.3: Trustworthy runs (2–4 weeks)

**Goal:** every number Cohorte prints comes with its uncertainty, and the full skill works end to end.

- **Ensembles:** `run --seeds 5` runs the scenario several times in parallel. `stats` and `compare` report means
  with 90% intervals, and flag any delta that falls inside the noise.
- **Noise-floor report:** for each run, how much the metrics move between seeds. `compare` refuses to call a winner
  below that floor.
- **Report as an artifact:** Claude writes `report.md` (and an HTML version), with each claim linked to the post ids
  or metrics behind it. A `verify-report` command checks that every quote exists in the transcript.
- **Checks on how the crowd behaves:** tune opinion dynamics so that sentiment can actually move. Test with
  synthetic "obvious" scenarios, such as an outrageous price that must polarise and a free gift that must please.
- **Hardening:**
  - test the Claude API backend for real;
  - a 48-hour, 10,000-agent run;
  - rate-limit backoff;
  - `--budget $X`, which stops before the money is spent.
- **Dogfood:** run Cohorte on 3 real launches (ours or friends') and publish the reports.

**Proof gate:**
- (a) the top 3 objections are the same in ≥4 of 5 seeds;
- (b) in a planted A/B test (variant B removes the objection), B wins in ≥90% of ensembles;
- (c) zero fabricated quotes in `verify-report`.

## V1: Proven (1–2 months)

**Goal:** show publicly that Cohorte anticipates real reactions better than the alternatives a team already has.

- **Backtest harness** (`cohorte backtest`):
  - replay historical cases using only the information available *before* the event, then score the predictions
    against what really happened;
  - use 20–30 public cases with documented reactions: pricing changes, rebrands, terms-of-service changes, product
    launches, viral backlashes and quiet flops;
  - freeze the seeds of each case to *before* the model's training cutoff where possible, or use cases after the
    cutoff, so the model can't simply remember the outcome.
- **Metrics:**
  - **objection recall**: did we surface the objections that actually dominated?
  - **direction**: positive or negative overall?
  - **ranking**: which of two variants did better?
  - **segment split**: who got angry?
- **Baselines to beat:**
  - one LLM prompt ("how will people react?");
  - a few human marketers predicting blind;
  - simple sentiment heuristics.
- **Public scorecard** in the README, versioned, with failures included.
- **Licensing:** decide now. Either relicense under AGPL-3.0 or do a clean-room rewrite of the prompts and algorithms
  adapted from MiroFish. This must be done **before any paid customer**.

**Proof gate:** Cohorte beats the single-prompt baseline on objection recall and variant ranking across 20+ cases,
and the difference is statistically significant. **If it fails,** narrow the claim to what *does* work (for example
"objection discovery and message testing" instead of "prediction") and rerun the gate. Don't build V2 on an
unproven claim.

**Business:** a launch post with the scorecard, an open-source community, and 3–5 design partners from the
waiting list.

## V2: Grounded in your customers (2–3 months)

**Goal:** the robots look like *your* market, not the model's stereotypes. That grounding is what companies will
pay for.

- **Data import:** survey exports, NPS verbatims, app-store and G2 reviews, support tickets, CRM segments, product
  analytics and social-listening exports. Archetypes and their proportions are fitted to this data rather than
  invented.
- **Calibration loop:** compare the simulated answers to probes against the real survey answers for the same
  segments, and adjust archetype parameters until they match. Report a calibration score per segment.
- **More channels:** LinkedIn (B2B), app-store reviews, email and newsletters, press and journalists, community
  forums. Add a **B2B buying committee** mode (champion, finance, security, end users).
- **Persistent cohorts:** reuse a company's calibrated population from one run to the next (a "standing panel").
- **Privacy:** PII scrubbing on import, local-only mode, and clear documentation of what is sent to the model.

**Proof gate:**
- grounded populations score better than ungrounded ones on V1 backtests;
- for design partners, simulated probe answers fall within ±10% of their real survey results.

**Business:** 10–20 paid pilots (€2–10k each). Main use cases are pricing changes, launch messaging and
terms-of-service or policy changes.

## V3: Hosted product (3–4 months)

**Goal:** product, marketing and comms teams use Cohorte without a terminal.

- **Web app:**
  - a guided setup (upload or paste materials, pick the audience);
  - live run view, dashboard and report viewer;
  - shareable links;
  - interviews in a chat interface;
  - an A/B workspace.
- **Teams:** workspaces, roles, comments on reports, history of decisions.
- **Infrastructure:**
  - hosted runs with queues;
  - multi-model routing (Haiku for crowds, Opus for key voices and reports);
  - cost caps per workspace.
- **Trust:** a path to SOC 2 Type I, a DPA, data retention controls, an EU data-residency option, and no training on
  customer data.
- **Pricing (initial):**
  - Team €500–2,000/month (simulation credits);
  - Business €5–15k/month (calibrated panels, SSO);
  - Enterprise (custom).

**Proof gate:** ≥40% of pilots convert to paid, ≥60% of paying teams run a simulation every month, and reports
are cited in real go/no-go decisions.

**Business:** about $1M ARR. Seed round on the strength of the scorecard plus retention.

## V4: Platform and learning loop (6–9 months)

**Goal:** become more accurate with every customer. This is the moat.

- **Outcome tracking:** after a launch, connect real results (analytics, social listening, survey, churn) and
  automatically score the forecast. The scoring feeds the calibration of archetypes for each industry and market.
- **Vertical packs** with pre-calibrated populations and templates:
  - pricing and monetisation;
  - PR and crisis comms;
  - B2B go-to-market;
  - consumer brand creative;
  - HR and internal change;
  - public policy.
- **Integrations:** Figma and Webflow (test a page before it goes live), Notion and Google Docs (test a draft),
  HubSpot and Salesforce (segments), Slack (results delivered to the team), plus a public API for agencies.
- **Agency program:** resellable white-label reports for marketing and PR agencies.
- **Continuous checks:** a pre-launch check that runs automatically in the content workflow, not just one-off
  studies.

**Proof gate:**
- measured accuracy improves version over version from outcome data;
- net revenue retention above 120%.

**Business:** about $10M ARR. Series A.

## V5: System of record for decisions (12–24 months)

**Goal:** no significant launch, price change or public statement ships without a Cohorte rehearsal.

- **Market models:** calibrated population models per country and segment, kept fresh from continuous public
  signals.
- **Decision ledger:** each decision is logged with its forecast and real outcome. This gives an auditable record
  for boards and regulators.
- **Regulated and high-stakes use:** public consultations, pharma and finance communications (with compliance
  review), elections-adjacent work explicitly *excluded* by policy.
- **Outcome-based pricing** for large accounts, such as paying for forecasts that prove right.

**Proof gate:** published accuracy at industry level with external validation (an academic or audit partner), and
large customers who commit decisions to it.

**Business:** $50–100M ARR at a typical 10–20× multiple gives a valuation of **$1B+**.

---

## Principles that hold across all versions

1. **No number without its uncertainty and its evidence.** False precision is the fastest way to lose trust.
2. **Publish the scorecard, including the failures.** It's both the marketing and the moat.
3. **The learning loop is the product.** Simulation alone can be copied; calibrated accuracy from outcome data
   can't.
4. **Safety by design:**
   - never impersonate real people;
   - label every simulated statement as simulated;
   - refuse use for manipulation or election persuasion.
5. **Kill criteria are real:**
   - if V1 can't beat the single-prompt baseline, narrow the promise before spending on V2/V3;
   - if V3 retention is weak, go back to a narrower vertical where accuracy is highest.

## Immediate next steps (v0.3, in order)

1. Ensembles with intervals and a noise floor (`--seeds`).
2. An end-to-end skill run on a real project, with `report.md` and `verify-report`.
3. Tune crowd opinion dynamics and add the "obvious scenario" tests.
4. Budget cap and Claude API hardening, and a 48-hour, 10,000-agent run.
5. Start collecting the 20–30 historical backtest cases for V1, and settle the license question.

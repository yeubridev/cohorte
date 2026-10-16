# Objection-discovery evaluation protocol (pre-registered 2026-10-16, before any data was collected)

This tests the claim Cohorte kept after the V1 rating test failed: it surfaces the objections people really raise.
This file is committed before the cases are collected and before anything is run.

## Data (to be collected after this commit)

- **Cases:** public announcements made between 2026-07-01 and 2026-10-10, such as price changes, policy or terms
  changes, product or feature launches, and rebrands. Each one had a public reaction that was documented: news
  coverage summarising what people complained about.
- **Variant:** the announcement as people first saw it (official post, email or press release), paraphrased
  closely, with **nothing** from the reaction.
- **Ground truth:** the 2–5 dominant complaints named in post-announcement coverage, with source URLs. They are
  written as short labels with alternative phrasings.
- **Target:** at least 15 cases, from at least 2 independent coverage sources each where possible. Every case is
  `split: "eval"` and `leak_safe: true`; the leak check still runs.

## Method (fixed)

- Same model (Claude Haiku 5.5) and 2 runs.
- The default audience panel, unless the case names the affected audience in its announcement context.
- Cohorte's top 8 merged dislikes vs the single prompt's top 8 objections.
- A blind LLM judge scores both lists.

## Analysis (fixed)

- **Primary metric:** objection recall on all real complaints, Cohorte vs single prompt.
- **Primary test:** paired, per case. A sign test on cases where one method found more complaints than the other.
- **Reported also:**
  - recall with exact keyword matching;
  - per-case results;
  - the share of cases where each method found the *top* complaint.
- **Claim rule:**
  - If Cohorte's recall is higher with sign-test p < 0.05, the README may say it finds real objections
    better than one prompt.
  - If not, the README says the two are equivalent, or that the prompt is better, as measured.

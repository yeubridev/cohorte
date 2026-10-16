# Benchmarks: proving that the cohort predicts

`backtest` replays cases whose outcome is known. For each case, Cohorte and a single-prompt baseline rate
every variant, using the **same model and the same material**. Both are then scored against what really
happened.

```bash
python3 scripts/cohorte.py backtest benchmarks/sanity --dry-run                       # count LLM calls
python3 scripts/cohorte.py backtest benchmarks/sanity --backend claude-cli --model haiku --budget 3
python3 scripts/cohorte.py backtest my-cases/ --runs 3 --calibrate                    # real cases feed calibration
```

The output is `scorecard.md` and `scorecard.json` in `.cohorte/backtests/<suite>/`. Re-running the command
resumes where it stopped, and finished cases cost nothing again.

**Speed:**
- `--parallel N` runs N cases at a time (default 4).
- Each case builds a *panel-only* cohort: personas and crowd archetypes, with no LLM-made time, activity or event
  configs. Those are only needed for a social `run`.

## Suites

| Suite | What it proves | Status |
|---|---|---|
| `sanity/` | The engine detects planted, obvious differences: an outrageous price, a clickbait article, an unpaid job ad. | 8 synthetic cases; gate ≥ 90% |
| *real cases (to collect)* | Cohorte anticipates real reactions better than one prompt does. **This is the V1 proof gate.** | 0 / 20 needed |

Synthetic cases never count as proof and never feed calibration.

## Case format

There is one JSON file per case, or a folder `<case>/case.json` with `seed` files next to it.

```json
{
  "id": "streaming-price-2027",
  "kind": "pricing",
  "title": "Streaming service price increase",
  "context": "Neutral description shared by all variants: who the company is, the market, the audience.",
  "date": "2027-02",
  "leak_safe": true,
  "source": "https://... (where the outcome is documented)",
  "audience": [
    {"name": "Current subscribers", "count": 400, "summary": "..."},
    {"name": "Price-sensitive students", "count": 200, "summary": "..."},
    {"name": "Tech journalist", "count": 1, "summary": "..."}
  ],
  "variants": [
    {"id": "A", "seed": "a.md"},
    {"id": "B", "seed_text": "What people saw for variant B, verbatim."}
  ],
  "outcome": {
    "metric": "churn in the following month (%)",
    "scale": [0, 20],
    "higher_is_better": false,
    "values": {"A": 4.1, "B": 9.8},
    "winner": "A",
    "objections": [["price", "expensive"], ["password sharing"], ["ads"]]
  }
}
```

- **`context`** is used to build the panel. It must not describe any one variant, so that the personas can't
  favour one.
- **`variants`** hold what people actually saw, as close to verbatim as possible. A case with a single variant
  only counts towards rank correlation and objection recall.
- **`outcome`**:
  - give at least one of `winner`, `values` or `objections`;
  - when `values` are given, `winner` is derived from them, using `higher_is_better`;
  - each objection is a list of alternative phrasings, and it counts as found when every word of one phrasing
    appears in a single predicted objection.
- **`audience`** is optional. Without it, a generic panel is used: target audience, general public, skeptics and a
  critic.

## Collecting real cases (the V1 work)

Good cases have **two or more variants shown to comparable audiences** and a **documented, quantified outcome**:

- A/B tests that were published (pricing pages, email subject lines, landing pages, ad creatives);
- price or terms changes with a reported churn or backlash, compared with similar changes that went smoothly;
- launches of competing products in the same week, with their store ratings or sign-ups;
- films, books or games released in the same period, with their audience ratings;
- your own and your design partners' experiments: the best kind, because the model can't have seen them.

**Leakage:** the model may already know famous outcomes. Use these rules:

- mark `leak_safe: true` only for outcomes after the model's training cutoff, or for private data;
- anonymise the brand names in `seed_text` when you can;
- the scorecard reports leak-safe cases separately, and only those really prove prediction.

**Honesty rules:**

- include the cases where Cohorte fails;
- never edit a case after seeing its result;
- add new cases in batches, and publish the scorecard of each batch.

## Gate

| Gate | Condition |
|---|---|
| Sanity (v0.3) | Winner accuracy ≥ 90% on the synthetic suite. |
| Proof (V1) | On ≥ 20 real cases with a known winner, Cohorte's winner accuracy beats the baseline's with exact McNemar p < 0.05. Rank correlation and objection recall are reported alongside. |

If the proof gate fails, narrow the claim to what does work (for example objection discovery) before building
further. That is the roadmap's kill criterion.

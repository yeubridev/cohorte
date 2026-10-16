# Cohorte scorecard · 2026-10-16T13:19Z
Model claude-haiku-5-5 (claude-cli) · 2 run(s) per variant · 8 case(s) · objections scored by blind LLM judge (top 8 per variant for both methods) · cost $0.63

## Verdict
- **Real-world proof: no real cases**: No real historical cases yet: nothing is proven about real-world prediction. Add cases with known outcomes (see benchmarks/README.md).
- **Sanity gate: pass**: planted differences detected in 8/8 synthetic cases (gate: ≥90%)

## Synthetic sanity cases (8)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (8 cases) | 100% | 100% |
| objection recall (40 objections) | 92% | 90% |
| rank correlation with real metric (0 points) | – | – |

Paired test on winners: Cohorte alone right 0×, baseline alone right 0× · exact McNemar p = 1.0

## Cases
| case | kind | type | real winner | Cohorte (potential per variant) | baseline | objections found (C / B) |
|---|---|---|---|---|---|---|
| sanity-ad-gym | ad | synthetic | A | A 38 · B 29 ✓ | A 41 · B 30 ✓ | 3 / 5 of 5 |
| sanity-app-onboarding | app | synthetic | A | A 37 · B 21 ✓ | A 38 · B 20 ✓ | 5 / 5 of 5 |
| sanity-article-quality | article | synthetic | A | A 40 · B 14 ✓ | A 49 · B 13 ✓ | 4 / 3 of 5 |
| sanity-job-offer | job_offer | synthetic | B | A 16 · B 47 ✓ | A 12 · B 47 ✓ | 5 / 5 of 5 |
| sanity-landing-clarity | website | synthetic | B | A 21 · B 47 ✓ | A 17 · B 49 ✓ | 5 / 5 of 5 |
| sanity-meal-app-price | pricing | synthetic | A | A 46 · B 24 ✓ | A 55 · B 21 ✓ | 5 / 5 of 5 |
| sanity-product-backpack | product | synthetic | B | A 17 · B 42 ✓ | A 16 · B 54 ✓ | 5 / 4 of 5 |
| sanity-video-hook | video | synthetic | A | A 38 · B 15 ✓ | A 41 · B 20 ✓ | 5 / 4 of 5 |

## How to read this
- Both methods see exactly the same material and use the same model; only the method differs.
- Synthetic cases have planted, obvious differences: passing them shows the engine can detect a difference, not that it predicts reality.
- Only real cases with outcomes the model can't have memorised (leak-safe) prove prediction. The gate needs ≥20 of them and p < 0.05.
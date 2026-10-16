# Cohorte scorecard · 2026-10-16T17:33Z
Model claude-haiku-5-5 (claude-cli) · 2 run(s) per variant · 15 case(s) · objections scored by blind LLM judge (top 8 per variant for both methods) · cost $0.82

## Verdict
- **Objection discovery vs one prompt: equivalent**: objection recall 67% vs 65% on 14 leak-safe cases; per case 4 vs 2, sign test p = 0.6875

## Evaluation split (unseen before the run) (15)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| objection recall (52 objections) | 64% | 62% |
| top complaint found | 73% | 73% |
| objection recall, exact keywords | 6% | 15% |

Per case: Cohorte found more complaints in 4 case(s), the baseline in 2 · sign test p = 0.6875

## All real cases (15)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| objection recall (52 objections) | 64% | 62% |
| top complaint found | 73% | 73% |
| objection recall, exact keywords | 6% | 15% |

Per case: Cohorte found more complaints in 4 case(s), the baseline in 2 · sign test p = 0.6875

## Leak-safe real cases (counted for proof) (14)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| objection recall (48 objections) | 67% | 65% |
| top complaint found | 79% | 79% |
| objection recall, exact keywords | 6% | 15% |

Per case: Cohorte found more complaints in 4 case(s), the baseline in 2 · sign test p = 0.6875

## Cases
| case | kind | type | real winner | Cohorte (potential per variant) | baseline | objections found (C / B) |
|---|---|---|---|---|---|---|
| obj-2026-fpf-logo | generic | real, leak-safe | – | announcement 32 | announcement 31 | 2 / 2 of 3 |
| obj-2026-playstation-discs | policy | real, leak-safe | – | announcement 46 | announcement 38 | 4 / 3 of 4 |
| obj-2026-cfb27-microtransactions | game | real, leak-safe | – | announcement 34 | announcement 37 | 2 / 0 of 3 |
| obj-2026-meta-muse-image | product | real, leak-safe | – | announcement 35 | announcement 36 | 3 / 3 of 4 |
| obj-2026-chatgpt-desktop-app | app | real, leak-safe | – | announcement 37 | announcement 40 | 2 / 1 of 4 |
| obj-2026-ac-black-flag-resynced-dlc | game | real, leak-safe | – | announcement 43 | announcement 43 | 0 / 3 of 3 |
| obj-2026-samsung-health-ai-consent | policy | real, leak-safe | – | announcement 38 | announcement 26 | 3 / 3 of 3 |
| obj-2026-google-assistant-shutdown | product | real | – | announcement 33 | announcement 30 | 1 / 1 of 4 |
| obj-2026-jetstar-overhead-locker-fee | pricing | real, leak-safe | – | announcement 50 | announcement 49 | 2 / 3 of 3 |
| obj-2026-sharks-rugby-rebrand | generic | real, leak-safe | – | announcement 37 | announcement 33 | 3 / 3 of 4 |
| obj-2026-claude-watermark | policy | real, leak-safe | – | announcement 48 | announcement 45 | 3 / 3 of 4 |
| obj-2026-dawnwalker-console-fps | game | real, leak-safe | – | announcement 47 | announcement 51 | 1 / 1 of 2 |
| obj-2026-apple-tv-price | pricing | real, leak-safe | – | announcement 48 | announcement 48 | 2 / 2 of 4 |
| obj-2026-xbox-cloud-gaming-cap | pricing | real, leak-safe | – | announcement 51 | announcement 49 | 3 / 2 of 3 |
| obj-2026-reddit-rss-old-reddit | website | real, leak-safe | – | announcement 33 | announcement 42 | 2 / 2 of 4 |

## How to read this
- Both methods see exactly the same material and use the same model; only the method differs.
- Synthetic cases have planted, obvious differences: passing them shows the engine can detect a difference, not that it predicts reality.
- Only real cases with outcomes the model can't have memorised (leak-safe) prove prediction. The gate needs ≥20 of them and p < 0.05.
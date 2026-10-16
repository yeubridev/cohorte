# Cohorte scorecard · 2026-10-16T14:46Z
Model claude-haiku-5-5 (claude-cli) · 2 run(s) per variant · 8 case(s) · objections scored by blind LLM judge (top 8 per variant for both methods) · cost $0.69

## Verdict
- **Real-world proof: not proven**: 8 leak-safe real case(s) with a known winner (gate needs ≥20; 8 real in total)

## Real cases (8)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (8 cases, chance 48%) | 25% | 38% |
| objection recall (0 objections) | – | – |
| rank correlation with real metric (17 points) | 0.104 | -0.428 |

Paired test on winners: Cohorte alone right 1×, baseline alone right 2× · exact McNemar p = 1.0
Correlation gap (Cohorte − baseline): +0.512 (90% bootstrap +0.065 to +0.981)

## Leak-safe real cases (8)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (8 cases, chance 48%) | 25% | 38% |
| objection recall (0 objections) | – | – |
| rank correlation with real metric (17 points) | 0.104 | -0.428 |

Paired test on winners: Cohorte alone right 1×, baseline alone right 2× · exact McNemar p = 1.0
Correlation gap (Cohorte − baseline): +0.512 (90% bootstrap +0.065 to +0.981)

## Cases
| case | kind | type | real winner | Cohorte (potential per variant) | baseline | objections found (C / B) |
|---|---|---|---|---|---|---|
| films-2026-07-03 | movie | real, leak-safe | young-washington | minions-and-monsters 46 · young-washington 41 ✗ | minions-and-monsters 46 · young-washington 40 ✗ | – |
| films-2026-07-10 | movie | real, leak-safe | moana | moana 46 · evil-dead-burn 41 ✓ | moana 44 · evil-dead-burn 51 ✗ | – |
| films-2026-08-14 | movie | real, leak-safe | paw-patrol-dino-movie | end-of-oak-street 44 · paw-patrol-dino-movie 37 ✗ | end-of-oak-street 46 · paw-patrol-dino-movie 39 ✗ | – |
| films-2026-08-21 | movie | real, leak-safe | mutiny | insidious-out-of-the-further 43 · mutiny 41 ✗ | insidious-out-of-the-further 46 · mutiny 45 ✗ | – |
| films-2026-08-28 | movie | real, leak-safe | coyote-vs-acme | coyote-vs-acme 39 · the-dog-stars 41 ✗ | coyote-vs-acme 46 · the-dog-stars 45 ✓ | – |
| films-2026-09-04 | movie | real, leak-safe | by-any-means | by-any-means 42 · onslaught 43 ✗ | by-any-means 41 · onslaught 56 ✗ | – |
| films-2026-09-11 | movie | real, leak-safe | runner | practical-magic-2 46 · runner 44 ✗ | practical-magic-2 45 · runner 46 ✓ | – |
| films-2026-09-25 | movie | real, leak-safe | forgotten-island | heart-of-the-beast 45 · primetime 38 · forgotten-island 49 ✓ | heart-of-the-beast 41 · primetime 46 · forgotten-island 49 ✓ | – |

## How to read this
- Both methods see exactly the same material and use the same model; only the method differs.
- Synthetic cases have planted, obvious differences: passing them shows the engine can detect a difference, not that it predicts reality.
- Only real cases with outcomes the model can't have memorised (leak-safe) prove prediction. The gate needs ≥20 of them and p < 0.05.
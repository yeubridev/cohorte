# Cohorte scorecard · 2026-10-16T15:06Z
Model claude-haiku-5-5 (claude-cli) · 2 run(s) per variant · 20 case(s) · objections scored by blind LLM judge (top 8 per variant for both methods) · cost $1.96

## Verdict
- **Real-world proof: not proven**: 12 leak-safe real case(s) with a known winner (gate needs ≥20; 20 real in total)

## Evaluation split (unseen before the run) (12)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (12 cases, chance 39%) | 50% | 50% |
| objection recall (0 objections) | – | – |
| rank correlation with real metric (32 points) | 0.036 | 0.247 |

Paired test on winners: Cohorte alone right 0×, baseline alone right 0× · exact McNemar p = 1.0
Correlation gap (Cohorte − baseline): -0.213 (90% bootstrap -0.454 to +0.022)

## Dev split (seen: never proof) (8)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (8 cases, chance 48%) | 25% | 38% |
| objection recall (0 objections) | – | – |
| rank correlation with real metric (17 points) | 0.072 | 0.075 |

Paired test on winners: Cohorte alone right 1×, baseline alone right 2× · exact McNemar p = 1.0
Correlation gap (Cohorte − baseline): -0.008 (90% bootstrap -0.528 to +0.47)

## All real cases (20)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (20 cases, chance 42%) | 40% | 45% |
| objection recall (0 objections) | – | – |
| rank correlation with real metric (49 points) | 0.083 | 0.217 |

Paired test on winners: Cohorte alone right 1×, baseline alone right 2× · exact McNemar p = 1.0
Correlation gap (Cohorte − baseline): -0.128 (90% bootstrap -0.33 to +0.076)

## Leak-safe real cases (counted for proof) (12)
| metric | Cohorte | single-prompt baseline |
|---|---|---|
| winner accuracy (12 cases, chance 39%) | 50% | 50% |
| objection recall (0 objections) | – | – |
| rank correlation with real metric (32 points) | 0.036 | 0.247 |

Paired test on winners: Cohorte alone right 0×, baseline alone right 0× · exact McNemar p = 1.0
Correlation gap (Cohorte − baseline): -0.213 (90% bootstrap -0.454 to +0.022)

## Cases
| case | kind | type | real winner | Cohorte (potential per variant) | baseline | objections found (C / B) |
|---|---|---|---|---|---|---|
| films-2026-07-03 | movie | real | young-washington | minions-and-monsters 52 · young-washington 47 ✗ | minions-and-monsters 57 · young-washington 52 ✗ | – |
| films-2026-07-10 | movie | real | moana | moana 52 · evil-dead-burn 48 ✓ | moana 52 · evil-dead-burn 57 ✗ | – |
| films-2026-08-14 | movie | real | paw-patrol-dino-movie | end-of-oak-street 50 · paw-patrol-dino-movie 42 ✗ | end-of-oak-street 52 · paw-patrol-dino-movie 53 ✓ | – |
| films-2026-08-21 | movie | real | mutiny | insidious-out-of-the-further 47 · mutiny 47 ✗ | insidious-out-of-the-further 52 · mutiny 47 ✗ | – |
| films-2026-08-28 | movie | real | coyote-vs-acme | coyote-vs-acme 45 · the-dog-stars 47 ✗ | coyote-vs-acme 45 · the-dog-stars 47 ✗ | – |
| films-2026-09-04 | movie | real | by-any-means | by-any-means 49 · onslaught 50 ✗ | by-any-means 50 · onslaught 57 ✗ | – |
| films-2026-09-11 | movie | real | runner | practical-magic-2 53 · runner 50 ✗ | practical-magic-2 52 · runner 53 ✓ | – |
| films-2026-09-25 | movie | real | forgotten-island | heart-of-the-beast 51 · primetime 45 · forgotten-island 55 ✓ | heart-of-the-beast 52 · primetime 50 · forgotten-island 57 ✓ | – |
| films-2026-extra-the-odyssey-vs-resident-evil-2026 | movie | real, leak-safe | the-odyssey | the-odyssey 58 · resident-evil-2026 50 ✓ | the-odyssey 67 · resident-evil-2026 57 ✓ | – |
| films-2026-extra-spider-man-brand-new-day-vs-verity | movie | real, leak-safe | spider-man-brand-new-day | spider-man-brand-new-day 57 · verity 43 ✓ | spider-man-brand-new-day 63 · verity 47 ✓ | – |
| films-2026-extra-digger-vs-other-mommy-vs-the-social-reckoning | movie | real, leak-safe | the-social-reckoning | digger 50 · other-mommy 49 · the-social-reckoning 49 ✗ | digger 53 · other-mommy 57 · the-social-reckoning 55 ✗ | – |
| games-2026-07-wk2 | game | real, leak-safe | denshattack | moonlight-peaks 42 · backyard-baseball 40 · denshattack 39 ✗ | moonlight-peaks 50 · backyard-baseball 52 · denshattack 47 ✗ | – |
| games-2026-07-wk5 | game | real, leak-safe | halo-campaign-evolved | halo-campaign-evolved 48 · mistfall-hunter 43 ✓ | halo-campaign-evolved 50 · mistfall-hunter 42 ✓ | – |
| games-2026-08-wk1 | game | real, leak-safe | big-walk | big-walk 39 · beast-of-reincarnation 44 · marvel-tokon 44 ✗ | big-walk 50 · beast-of-reincarnation 52 · marvel-tokon 50 ✗ | – |
| games-2026-08-wk3 | game | real, leak-safe | sinking-city-2 | hell-let-loose-vietnam 43 · sinking-city-2 42 · mortal-shell-2 43 ✗ | hell-let-loose-vietnam 50 · sinking-city-2 48 · mortal-shell-2 48 ✗ | – |
| games-2026-08-wk5 | game | real, leak-safe | star-wars-zero-company | star-wars-zero-company 47 · captain-tsubasa-2 43 · serious-sam-shatterverse 45 ✓ | star-wars-zero-company 53 · captain-tsubasa-2 40 · serious-sam-shatterverse 47 ✓ | – |
| games-2026-09-wk1 | game | real, leak-safe | onimusha-way-of-the-sword | blood-of-dawnwalker 49 · onimusha-way-of-the-sword 47 · nba-2k27 38 ✗ | blood-of-dawnwalker 58 · onimusha-way-of-the-sword 53 · nba-2k27 50 ✗ | – |
| games-2026-09-wk2 | game | real, leak-safe | trails-in-the-sky-2nd | halloween-the-game 38 · trails-in-the-sky-2nd 47 ✓ | halloween-the-game 43 · trails-in-the-sky-2nd 57 ✓ | – |
| games-2026-09-wk4 | game | real, leak-safe | control-resonant | control-resonant 45 · silent-hill-townfall 43 · minecraft-dungeons-2 42 ✓ | control-resonant 53 · silent-hill-townfall 52 · minecraft-dungeons-2 50 ✓ | – |
| games-2026-10-wk1 | game | real, leak-safe | retrospace | ace-combat-8 46 · retrospace 43 · mxgp-26 38 ✗ | ace-combat-8 47 · retrospace 48 · mxgp-26 50 ✗ | – |

## How to read this
- Both methods see exactly the same material and use the same model; only the method differs.
- Synthetic cases have planted, obvious differences: passing them shows the engine can detect a difference, not that it predicts reality.
- Only real cases with outcomes the model can't have memorised (leak-safe) prove prediction. The gate needs ≥20 of them and p < 0.05.
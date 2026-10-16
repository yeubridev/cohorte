# V1 evaluation protocol (pre-registered 2026-10-16, before the evaluation run)

This file is committed **before** the evaluation cases are run. Any change to it after the run has to be
reported as a deviation.

## Data

| Split | Cases | Status when this was written |
|---|---|---|
| `dev` | `films-2026/cases.json`: 8 groups, 17 films | **seen**: run once with the `potential` target |
| `eval` | `films-2026/extra-cases.json` (3 groups, 7 films) and `games-2026/cases.json` (9 groups, 25 games) | **unseen**: never run |

- All of these films and games were released between July and October 2026. They come with pre-release material
  only, and their outcomes are sourced; see each case's `sources` field.
- The values come from web-search snapshots. A few were spot-checked:
  - Coyote vs. Acme, A;
  - The Dog Stars, C+;
  - Mutiny, B+;
  - Forgotten Island, A;
  - Big Walk, 96%;
  - Marvel Tōkon, 42–49% across snapshots, which gives the same rank within its group.

## What the dev run taught us (the one change made)

On the dev films, both methods were below chance at picking the winner: Cohorte 25%, single prompt 38%, chance 48%.
Both were predicting the wrong quantity. CinemaScore and Steam reviews measure the satisfaction of **people who
chose** the film or game, not the appeal to everyone.

**Change:** cases whose outcome is rated by people who chose the subject use `outcome.target: "satisfaction"`.
- **Cohorte:** each respondent's satisfaction dimensions (enjoyment, recommend, rating; for games, fun, retention,
  recommend) are weighted by the people it stands for times its intent to watch or buy.
- **Single prompt:** asked for the average answers "of the people who actually choose it", on the same
  dimensions.

Nothing else changes: same model (Haiku 5.5), 2 runs per variant, default audience panel, seeds and prompts.

## Analysis, fixed in advance

- **Primary metric:** winner accuracy on the **eval** split (12 cases), Cohorte vs single prompt, with an exact
  McNemar test.
- **Secondary metric:** Spearman correlation between predicted and real values on the eval split, with a 90%
  bootstrap interval on the gap.
- **Reported apart:** the dev split, re-scored with the satisfaction target. It is labelled *seen* and never counts
  as proof.
- **Proof gate:** at least 20 leak-safe cases. The eval split has only 12, so **this run cannot prove V1**, whatever
  the result. It tells us whether the method is worth collecting the next 20+ unseen cases for.
- **Leak check:** cases the model says it recognises are excluded from the leak-safe subset.

## Result (appended after the run, protocol unchanged)

The run was 2026-10-16, on Claude Haiku 5.5 through `claude -p`, and cost $1.96, including the dev reruns. The full
scorecard is [`results/v1-eval-2026-10-16-haiku.md`](results/v1-eval-2026-10-16-haiku.md).

| Eval split (12 unseen cases) | Cohorte | Single prompt | Chance |
|---|---|---|---|
| Winner accuracy | 50% (6/12) | 50% (6/12) | 39% |
| Paired test | no discordant case: the two methods picked the same winner every time | | p = 1.0 |
| Spearman with the real value (32 titles) | +0.04 | +0.25 | 0 |
| Gap in Spearman (Cohorte − prompt) | −0.21, 90% bootstrap interval −0.45 to +0.02 | | |

- **Leak check:** the model recognised 0 of the 32 titles.
- **Dev split, re-scored with the satisfaction target:** Cohorte 25%, single prompt 38%, chance 48%.

**Conclusion:**
- **V1 is not proven.** On real audience ratings, Cohorte doesn't beat a single prompt, and its ranking is, if
  anything, weaker.
- Per the roadmap's kill criterion, the claim is narrowed to rehearsal and objection discovery.
- Rating prediction gets another attempt only with grounding (V2) and new, unseen cases.

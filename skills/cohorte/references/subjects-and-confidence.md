# Testing any subject, and how much to trust the result

## 1. Subject kinds

`cohorte.py kinds` lists them. Each kind defines how a robot *experiences* the subject and which dimensions it
rates (0-10). Those dimensions are combined into a weighted **potential score (0-100)**.

| Kind | Use for | Dimensions (weight) |
|---|---|---|
| `app` | mobile or web apps, SaaS | usefulness, ease, install intent ×2, pay intent, retention, recommend |
| `website` | landing pages, home pages, repo pages | first impression, clarity, trust, click intent ×2, convert intent ×2 |
| `video` | YouTube, TikTok, podcasts, trailers | click intent, retention ×2, enjoyment, share intent, follow intent |
| `movie` / `series` | films, shows, episodes | watch or start intent ×2, enjoyment, recommend, rating / continue intent |
| `article` | blog posts, newsletters, press articles | click intent, read depth, credibility, value, share intent |
| `book`, `music`, `game` | creative works | buy, finish, replay or retention, enjoyment, recommend |
| `product`, `pricing` | physical or digital goods, price changes | need fit, price acceptance, buy or stay intent ×2, trust |
| `ad` | ad creatives, campaigns | attention, recall, liking, click intent ×2 |
| `pitch` | decks, memos, sales proposals | clarity, credibility, excitement, next-step intent ×2 |
| `job_offer` | job postings, employer brand | attractiveness, clarity, apply intent ×2, trust |
| `policy`, `event` | announcements, rules, scenarios, events | support or belief, fairness or approval, compliance or engagement ×2 |
| `generic` | anything else | appeal, relevance, clarity, act intent ×2, recommend |

If the world has no `probes`, the two highest-weight dimensions become the probes of the social simulation.

### Potential vs satisfaction: predict the right quantity

- **Potential:** appeal to the whole cohort. It's the right target for reach questions: who clicks, who installs,
  who buys, opening box office.
- **Satisfaction of choosers:** `assess` also prints this for movie, series, game, book, video, app and product. It
  weights each robot's satisfaction (enjoyment, recommend, rating; for games, fun and retention) by its intent to
  watch or buy. It's the right target for ratings given by the people who chose the subject: CinemaScore, Steam
  or app-store reviews, Goodreads.

Example: a children's film can have low potential with a general audience and still get an A from the families
who went. When you report or calibrate, use the score that matches the real outcome. Mixing them up made both
Cohorte and a single prompt score below chance on our first real films (`benchmarks/PROTOCOL.md`).

## 2. Ingesting the material

```bash
$COHORTE ingest <url-or-files...> --kind movie --out .cohorte/drafts/<name> --title "..." --notes "release date, price, channel"
```

| Source | What is extracted | Evidence level |
|---|---|---|
| URL or `.html` | title, meta description, headings, buttons and CTAs, image alt text, forms, visible text | full (>80 words) |
| `.srt` / `.vtt` | transcript, duration, words per minute | full (>150 words) |
| `.pdf` (needs `pypdf`), `.docx`, `.md`, `.txt` | text | full (>150 words) |
| images | listed with `DESCRIBE` markers | partial |
| videos | duration and 8 key frames (if ffmpeg is installed), listed with `DESCRIBE` markers | partial |
| `.csv` | first rows | partial |

**Your job after ingest:**
1. Open each listed image or frame with the Read tool.
2. Replace its `DESCRIBE` marker in `seed.md` with what a viewer sees: layout, text on screen, people, mood, pacing.
3. For films and videos, also add the synopsis, cast, trailer beats and price or channel.
4. Then `init ... --seed .../seed.md --subject .../subject.json`.

Robots only know what `seed.md` says, so the closer it is to the real thing, the better. If you only have a
description of the subject (an idea, a synopsis), the evidence level is `description` and confidence drops.

## 3. Assessment panel

```bash
$COHORTE assess <name> --runs 5 --models opus,sonnet   # or --dry-run first to count calls
```

- **Who answers:**
  - every individual robot (named people, delegates, small crowds), each standing for 1 person;
  - every crowd archetype, standing for its member count;
  - institutions answer as **stakeholders**: their verdicts are reported but they don't count in the score.
- **Runs:** each run uses a different exposure context (on a phone at night, sent by a friend, seen as an ad,
  found while searching…). Models rotate across runs.
- **Output:**
  - `assessment.md` and `assessment.json` in the simulation folder;
  - the potential score with the range across runs;
  - dimension means, per-segment scores, weighted likes and dislikes;
  - voices (the most negative and most positive verdicts, with what each person would actually do next);
  - the confidence index.
- **Cost:** (individual robots + archetypes) × runs calls. 21 respondents × 3 runs on Haiku 5.5 ≈ $0.06.

`assess` gives the static verdict: what people think when they meet the subject. `run` adds the dynamics: what
spreads, who influences whom, how opinion moves over days. Use both for launches. For a quick rating, `assess`
alone is enough.

## 4. Confidence index (0-100)

```
internal = 100 × (0.40 · stability + 0.25 · evidence + 0.20 · model agreement + 0.15 · coverage)
index    = min(internal, cap)
```

| Component | Measures | Value |
|---|---|---|
| stability | does the score hold across runs and contexts? | 1 − (standard deviation of run scores / 10); 0.3 with a single run |
| evidence | did the robots see the real thing? | full 1.0 · partial 0.7 · description 0.4 |
| model agreement | do different models agree? | 1 − (max gap between model means / 15); 0.5 if a single model |
| coverage | enough distinct respondents? | min(1, respondents / 20) |
| **cap** | **is this kind proven against reality?** | **60 until ≥5 real outcomes are recorded; then 60 + 40 × r × min(1, n / 30)** |

Grades: low < 45 ≤ moderate < 70 ≤ good < 85 ≤ high. **Uncalibrated results can never be better than
"moderate".** To approach 100 you need near-perfect correlation (r → 1) over 30+ recorded real outcomes of the
same kind.

### Rules when you report it
- Say what the index measures: *how consistent and well-founded* the prediction is, and *how proven* this kind of
  prediction is. It is **not** the probability that the prediction is right, unless the kind is calibrated. Then
  quote the calibrated estimate, its interval and its typical error instead.
- Never round a capped index up or describe an uncalibrated result as reliable. If the user asks for ~100%
  confidence, explain that no simulation of people reaches that on open-ended future events. Show them how the
  ceiling rises: record outcomes, calibrate, use full material and several models.
- Polarisation (segments that disagree strongly) is a finding, not low confidence. Report it as such.

## 5. Calibration: the only way to raise the ceiling

Once the real outcome is known (box-office rating, conversion rate, D30 retention, CTR…):

```bash
$COHORTE calibrate add --sim <name> --actual 7.4 --scale 0 10 --metric "IMDb rating"
$COHORTE calibrate fit --kind movie      # after ≥3 pairs; applied from ≥5
$COHORTE calibrate show
```

- The fit maps raw potential to real outcomes (linear, per kind, stored in `.cohorte/calibration/<kind>.json`).
- From then on `assess` prints a **calibrated estimate in the metric's own units** with an 80% interval and the
  typical error.
- **Avoid leakage:** famous past subjects may be known to the model. Prefer outcomes the model can't have seen
  (after its training cutoff, private launches, your own A/B tests).
- **In bulk:** `backtest <cases> --calibrate` records every real (non-synthetic) case with known values as a
  calibration pair, and also scores Cohorte against a single-prompt baseline (see `benchmarks/README.md`).

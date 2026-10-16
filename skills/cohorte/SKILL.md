---
name: cohorte
description: Swarm-intelligence rehearsal of how people will react to anything - a project, app, website, video, film, article, book, ad, product, pricing change, pitch, job posting, announcement, policy or event (a Claude re-implementation of MiroFish), with a potential score and an honest confidence index. Builds a world from the user's material, generates a cohort of persona "robots" (from a dozen to 10,000+) that post, like, repost, comment and argue on simulated Twitter- and Reddit-like platforms over simulated days, then writes a prediction report, lets the user interview any agent, and A/B-tests changes on the same population. Use when the user wants to test or pressure-test their project against simulated users, customers or public opinion, predict reception, find objections before launch, compare two versions of a message, run a MiroFish/OASIS-style social simulation, or talk to simulated personas.
---

# Cohorte — rehearse the future with a cohort of robots

Cohorte recreates the MiroFish pipeline (github.com/666ghj/MiroFish) as a skill. **You** do what MiroFish's
LLM services do around the simulation (understanding the seed, building the world, acting as the ReportAgent),
and a bundled Python engine runs the multi-agent simulation itself.

**Two tiers of robots** make large populations affordable (10,000 agents for 24 simulated hours ≈ 300 LLM calls):
- **LLM agents**: named actors (company, media, experts) and a few *delegates* per crowd. Each has a full
  persona and its own LLM call on every turn, like MiroFish/OASIS agents. These are the voices you interview.
- **Crowd members**: big segments (`count` above `crowd_threshold`, default 12) are split into LLM-designed
  archetypes and sampled into individual agents (own name, follows, feed, opinion, likes, comments, posts).
  Each round, **one LLM call per segment** reads what its online members see and returns per-archetype reaction
  probabilities, texts in their voice, mood and probe answers; every member then samples its own actions and
  updates its own opinion. Use `"tier": "llm"` on an entity to give every member its own LLM turn instead.

| MiroFish stage | Here |
|---|---|
| 1. Graph building: seed extraction, ontology, entities and relations | You read the project and write `seed.md` + `world.json` |
| 2. Environment setup: personas, time/event/agent configs | `setup` (engine, LLM) then you review with the user |
| 3. Dual-platform simulation (OASIS Twitter + Reddit) | `run` (engine) |
| 4. ReportAgent with retrieval tools | You, using `stats`, `timeline`, `search`, `thread`, `agent`, `interview` |
| 5. Deep interaction: chat with any agent | `interview` |
| *(added)* Adapt and re-test | `fork` + `run` + `compare` |

## Engine

```bash
COHORTE="python3 ${CLAUDE_SKILL_DIR}/scripts/cohorte.py"   # Python 3.9+, stdlib only
$COHORTE doctor          # which LLM backends work here
```

`$COHORTE` below is shorthand: shell variables do not persist between commands, so write the full
`python3 ${CLAUDE_SKILL_DIR}/scripts/cohorte.py ...` each time (`${CLAUDE_SKILL_DIR}` is the directory holding this
SKILL.md; if it was not substituted, use that directory's absolute path).

Simulations live in `./.cohorte/sims/<name>/` of the current project (`--home` to change). Suggest adding
`.cohorte/` to `.gitignore` unless the user wants to keep reports in git.

Backends (set at `init`, change later with `$COHORTE llm <name> --backend ... --model ...`):
- `anthropic`: Claude API through the official SDK (`pip install anthropic`, `ANTHROPIC_API_KEY`). Fastest, parallel.
- `claude-cli`: headless `claude -p`, reuses the user's Claude Code login (no API key). Works, ~10-30 s per call.
- `manual`: no network; the engine writes `pending_requests.json`, you answer each request in character into
  `responses.json`, then re-run the same command (exit code 3 means "waiting for you"). Use `--until-round N`, not
  `--max-rounds`, in this mode. For tiny simulations only, or where nothing else works; give each request its own
  answer so agents stay independent (subagents help).
- `mock`: offline fake data. Only for dry runs of the mechanics; never report mock output as a prediction.

Model: the engine defaults to `claude-opus-5-5`. The robots make hundreds of calls, so before running, show the
user the `estimate` for the default and for `--model haiku` (Claude Haiku 5.5, far cheaper) and let them choose.
Never switch model on your own for cost.

Speed: the engine itself handles 10,000 agents in ~1 s per simulated round; wall time is the LLM calls. With
`anthropic`, raise `--concurrency` (16-32) for big runs. `claude-cli` measured ~2.5 min for 3 rounds of a
10,000-agent world (≈ 70 calls at concurrency 8).

## Workflow

### 1. Frame the test
Work out, from the conversation and the repo (README, landing page, pricing, docs, changelog, issues):
- **What is tested**: the exact artifact (launch post, pricing page, feature, policy, pitch, story…).
- **The decision** it informs ("12 € or 6 €?", "ship with or without the data-sharing clause?").
- **Who matters**: target segments, adjacent skeptics, amplifiers (media, creators), competitors, institutions.
- **How many**: a few dozen LLM agents find objections; thousands of crowd members add realistic volume,
  virality and segment-level numbers (share of each archetype that would try it). Size crowds to the question.
- **The signal**: what would count as success or failure (try intent, sentiment, a specific objection dying out).

Turn it into a **prediction question**, MiroFish's "simulation requirement", e.g. *"Predict how indie developers,
tech media and competitors react in the 72 h after X launches at $9/month, which objections spread, and whether
the free tier limits become the main controversy."* Ask at most 2-3 questions, only for what you cannot infer.

### 1b. Ingest the subject (any format)
Pick the subject kind (`$COHORTE kinds`: app, website, video, movie, series, article, book, music, game,
product, pricing, ad, pitch, job_offer, policy, event, generic) and ingest the real material:
```bash
$COHORTE ingest <urls or files> --kind <kind> --out .cohorte/drafts/<name> --title "..." --notes "context"
```
It extracts pages, PDF/DOCX/text, subtitles and video key frames. Open every listed image or frame, describe
it in `seed.md` in place of its `DESCRIBE` marker, and add what the files can't show (synopsis, price, channel).
Then pass `--seed .../seed.md --subject .../subject.json` to `init`. Read `references/subjects-and-confidence.md`.

### 2. Seed and world (stage 1, you)
Read `references/world-building.md`, then write in a working folder (e.g. `.cohorte/drafts/<name>/`):
- `seed.md`: what the public can see (exact copy, pricing, FAQ, screenshots described in words) plus
  background facts. Agents only know what is here and in `world.json`.
- `world.json`: requirement, ontology (entity types), entities and crowds (`count`), relations, the exact
  `initial_posts` under test, optional `scheduled_events` (god's-eye injections), `probes`, `settings`.

```bash
$COHORTE validate world.json
$COHORTE init <name> --world world.json --seed seed.md [--seed more.md] --backend <b> [--model <m>]
```

### 3. Environment setup (stage 2)
```bash
$COHORTE setup <name>      # crowds -> individuals, personas, time/event/activity configs
$COHORTE show <name>       # roster, stances, kick-off posts, schedule (--full for personas)
$COHORTE estimate <name>   # exact number of agent turns + cost range
```
Show the user a compact cast table and the cost, and check the cast: missing voices? one-sided deck? Edit
`agents.json` / `config.json` directly for small fixes (they are plain JSON, see `references/engine.md`), or fix
`world.json` and `init --force` + `setup` again. **Get an explicit go before running on a paid backend.**

### 3b. Assess (rating panel, any subject)
```bash
$COHORTE assess <name> --dry-run                     # number of LLM calls
$COHORTE assess <name> --runs 5 [--models opus,sonnet]
```
Every robot and crowd archetype experiences the subject and rates it on its kind's dimensions. You get the
potential score (0-100), the spread across runs, per-segment scores, what works, what puts people off, voices,
and the **confidence index**. That index is capped at 60/100 until real outcomes calibrate the kind. Report it
exactly as the reference explains: never as a probability of being right, and never above what it is. When the
real outcome is known later, record it with `calibrate add` so the next predictions get better.

### 4. Simulate (stage 3)
```bash
$COHORTE run <name>                  # resumable; each round is saved
$COHORTE run <name> --max-rounds 12  # run in chunks to look at early results
$COHORTE inject <name> --at-hour 30 --content "..." [--poster <agent>]   # what-if mid-run
$COHORTE status <name>
```
Runs longer than a few minutes: start them in the background and check `status` instead of waiting in the
foreground. Ctrl-C or a crash loses at most the current round; re-run to resume (cached calls are not re-paid).

### 5. Report (stage 4, you are the ReportAgent)
Follow `references/report-guide.md`: survey with `stats` + `timeline`, plan 2-5 sections, then for **each**
section gather evidence with at least 3 tool calls mixing `search` (add `--thoughts` for the private reasoning
behind public behaviour), `thread`, `agent` and `interview`. Quote agents verbatim with handle, type, platform,
post id and simulated time. End with **What to change**: evidence → change → expected effect → how to re-test.
Save to `.cohorte/sims/<name>/report.md`, then give the user the headline prediction, the 3-5 key findings and the
recommended changes in chat, with the path to the report.

### 6. Deep interaction (stage 5)
```bash
$COHORTE interview <name> -q "What would make you start the trial tonight?" --agents 8,johanna
$COHORTE interview <name> -q "..." --type BusyParent --sample 4
$COHORTE interview <name> -q "And at 6 €?" --agents 8 --followup      # continues the conversation
```
Agents answer in character from their persona and their memory of the run. When the user wants to "talk to"
an agent, relay their questions this way and quote answers as given.

### 7. Adapt and re-test (the point of the exercise)
```bash
$COHORTE fork <name> <name>-v2 --posts new_posts.json --replace-posts [--seed new_seed.md]
$COHORTE run <name>-v2
$COHORTE compare <name> <name>-v2
```
`fork` keeps the exact same robots, configs and random activation schedule, so differences come from the change
(plus LLM noise). `new_posts.json` is `[{"poster": "<agent id/name>", "content": "..."}]`. Report deltas with
humility: small differences (< 0.1 sentiment, < 1 point on a 0-10 probe) are noise; repeat or lengthen runs before
acting on them. Help the user apply the winning change to their actual project.

## Ground rules
- Confidence is earned, not claimed. If someone asks for ~100% certainty, explain that no simulation of people
  reaches that on open-ended future events, and show how the ceiling rises: full material, several runs and
  models, and above all calibration against real outcomes.
- Everything in a report must come from the simulation's tools. Never invent quotes, counts or agents; say when
  the data is thin.
- Present results as a rehearsal, not a measurement: LLM agents are articulate, often more reasonable and more
  homogeneous than real crowds, and they only know the seed. Recommend real-user validation for big decisions.
- The seed and personas are sent to the LLM provider: leave out secrets and personal data the user did not
  intend to share.
- Prefer archetypes and fictional individuals. Real organisations and public figures may appear as entities,
  but label all their simulated statements as simulated, and never present them as real quotes.
- Report costs: `status` shows calls, tokens and estimated spend.

## Command reference
`doctor` · `validate <world.json>` · `init` · `llm` · `setup` · `show [--full]` · `estimate [--model]` ·
`run [--max-rounds N | --until-round N]` · `inject` · `status [name]` · `stats [--platform]` ·
`timeline [--bucket H]` · `search <query> [--thoughts]` · `agent <ref>` · `thread <post_id> --platform` ·
`export [--out file]` · `interview -q ... (--agents | --type [--sample] | --all) [--followup]` · `fork` · `compare`.
`ingest` · `kinds` · `assess [--runs N] [--models a,b] [--dry-run]` · `calibrate add|fit|show`.
`$COHORTE <command> -h` documents every flag. Files and tuning knobs: `references/engine.md`.

# Cohorte

**Rehearse your project's reception with a cohort of AI robots, then adapt it before real people see it.**

Cohorte is a Claude skill that recreates [MiroFish](https://github.com/666ghj/MiroFish), the swarm-intelligence
prediction engine, for Claude Code. Point it at your project (a launch post, a landing page, a pricing change, a
feature, a policy, a story). It builds a small world around it and generates a cohort of personas ("robots"):
target users, skeptics, media, creators, competitors, your own company account. They then live on simulated
Twitter-like and Reddit-like platforms for a few simulated days. They read their feeds, post, like, repost, quote,
comment, follow, search and argue.
You get:

- a **prediction report** grounded in what the robots actually did and privately thought, with quotes;
- **metrics**: sentiment per segment over time, custom probes ("how likely are you to try it this week? 0-10"),
  top posts, opinion shifts;
- **interviews**: ask any robot why it did what it did, in character;
- **A/B re-tests**: change your copy, price or policy and replay the *same* cohort to compare.

```
your project ──► seed.md + world.json ──► personas & config ──► simulation ──► report ──► interviews
  (Claude reads it)    (stage 1, Claude)       (stage 2, engine)    (stage 3)    (stage 4)   (stage 5)
                                                                        ▲                         │
                                                                        └──── fork + compare ◄────┘
                                                                             (adapt and re-test)
```

## From a dozen robots to 10,000+

Robots come in two tiers:

- **LLM agents** are named actors (your company, media, experts) plus a few *delegates* per crowd. Each one has a
  full persona and makes its own LLM call every time it is online. These are the voices you interview.
- **Crowd members** fill big segments (`"count": 4000`). Claude first designs the segment's archetypes, such as
  "single parent on a tight budget" or "policy-reading privacy hardliner". Members are then sampled from those
  archetypes as individual agents, each with its own name, follows, feed, opinion, likes, comments and posts.
  Each round costs **one LLM call per segment**, not one per member. That call returns reaction probabilities and
  texts in the archetype's voice, and each member samples its own actions from them. This is the "LLM archetypes"
  approach that large-population simulators use.

Measured results:

| Run | Agents | LLM calls | Engine time | Cost |
|---|---|---|---|---|
| Mock backend, 24 simulated hours | 10,000 (18 LLM + 9,982 crowd) | 353 instead of ~32,000 | ~29 s | free |
| Claude Haiku 5.5 via `claude -p`, 3 evening hours | 10,000 (13 LLM + 9,987 crowd) | 88 (setup included) | 2.5 min wall | $0.13 |
| Estimate, 24 h, 10,000 agents | Haiku 5.5 ≈ $0.35 · Opus 5.5 ≈ $14 | ≈ 300 | | one call per agent turn: ≈ $13 / ≈ $520 |

The engine itself was also made faster. Feeds are now ranked from a per-round candidate pool with cached
keywords, and state is saved compactly. A 2,000-agent run went from 68 s to 17 s of engine time.

## What the robots look like

From the bundled fictional example (an AI meal-planning app launching at 12 €/month), 10 agents, 4 simulated
evening hours on Claude Haiku 5.5, about $0.06:

> "Thank you for reading the fine print for us... A free trial always ends, so I would like to know plainly: is
> the card charged on its own after 7 days, and how do they handle a family's grocery and purchase data?"
> — @dimitra_kostopoulou, retired pharmacy technician, Twitter #9

> "Two weeks and a promo code is a personal experience, not a trial. 'Saved me 3 grocery runs' is a fine result
> for one household. It tells me nothing about diet adaptations for diabetes, CKD or pregnancy."
> — @amara_diallo, dietitian, Twitter #12

Interviewed afterwards ("what would make you start the free trial tonight?"), the privacy-minded agent listed
seven concrete conditions: a separate opt-in for partner data sharing, retention periods, a deletion path, and
others. Agents raised the auto-renewal and data-sharing objections on their own, before the scripted privacy news
item was injected. Those objections are the kind of thing you fix before launch.

## Install

**As a Claude Code plugin** (recommended):
```
/plugin marketplace add yeubridev/cohorte
/plugin install cohorte@cohorte
```

**As a plain skill**: copy `skills/cohorte/` to `~/.claude/skills/cohorte/` (all projects) or to
`<your-project>/.claude/skills/cohorte/` (one project).

**claude.ai**: zip the `skills/cohorte` folder and upload it as a skill. It needs code execution. Without network
access from the sandbox, use the `manual` backend: Claude answers each robot's prompt itself, which is only
practical for small runs.

Requirements: Python 3.9+, no mandatory dependencies. One LLM backend:

| Backend | What it needs | Notes |
|---|---|---|
| `anthropic` | `pip install anthropic` + `ANTHROPIC_API_KEY` (or `ant auth login`) | fastest, parallel, prompt caching |
| `claude-cli` | the `claude` command (your Claude Code login) | no API key; ~10-30 s per call |
| `manual` | nothing | Claude answers pending prompts itself; tiny runs only |
| `mock` | nothing | offline fake data to try the mechanics |

The engine defaults to `claude-opus-5-5`. Robots make hundreds of calls, so the skill shows you a cost estimate for
that model and for `haiku` (Claude Haiku 5.5, much cheaper) and lets you choose before anything runs.

## Use it

In Claude Code, inside your project, ask in plain words:

- "Use cohorte to test how freelance designers and agencies would react to our launch post in `marketing/launch.md`."
- "Simulate the reaction to moving from 9 €/month to 12 €/month for existing users. Which segments churn and why?"
- "Run the same cohort on version B of the landing headline and compare."
- "Interview the three most negative robots about what would change their mind."
- "Simulate 10,000 French parents and 2,000 privacy advocates reacting to the pricing page."

Claude frames the prediction question, reads your project to write the seed and cast, shows you the roster and
the cost, runs the simulation, writes the report (`.cohorte/sims/<name>/report.md`) and helps you adapt and re-test.

You can also drive the engine yourself:

```bash
COHORTE="python3 skills/cohorte/scripts/cohorte.py"
$COHORTE doctor
$COHORTE init demo --world skills/cohorte/assets/world.example.json --backend mock   # or claude-cli / anthropic
$COHORTE setup demo && $COHORTE show demo && $COHORTE estimate demo --model haiku
$COHORTE run demo
$COHORTE stats demo && $COHORTE timeline demo && $COHORTE search demo privacy --thoughts
$COHORTE interview demo -q "Would you pay 12 € a month? Why?" --type BusyParent --sample 3
$COHORTE fork demo demo-b --posts variant_posts.json --replace-posts && $COHORTE run demo-b
$COHORTE compare demo demo-b
```

## How it maps to MiroFish

| MiroFish | Cohorte |
|---|---|
| Ontology generation + Zep GraphRAG from seed documents | Claude writes `world.json` (same ontology rules: speaking actors only, ~10 types with `Person`/`Organization` fallbacks) and `seed.md` |
| OASIS profile generator (individual vs institutional personas) | `setup`: same persona structure, plus crowd archetypes expanded into distinct individuals |
| Simulation config generator (time / events / per-agent activity, LLM + rule fallbacks) | `setup`: same fields, batching, guards and kick-off post assignment |
| OASIS Twitter + Reddit environments, time-of-day agent activation | `run`: built-in platforms with the same action sets, feed ranking (recency, popularity, relevance, echo chamber, virality), notifications and memory, seeded activation |
| ReportAgent (ReACT with insight_forge, panorama_search, quick_search, interview_agents) | Claude follows the same method with `stats`, `timeline`, `search`, `thread`, `agent`, `interview` |
| Chat with any agent | `interview [--followup]` |
| – | private `thought` / `sentiment` / `probes` per turn, `inject` god's-eye events, `fork` + `compare` |

Details: [`skills/cohorte/references/engine.md`](skills/cohorte/references/engine.md).

## Limits

The robots are language-model personas. They are articulate, often more reasonable and more alike than real
crowds, and they only know what the seed tells them. Use Cohorte to find objections, compare variants and prepare
answers. It does not replace talking to real users. Treat small differences between runs as noise.

## Development

```bash
python3 -m unittest discover -s tests      # offline, uses the mock and manual backends
claude plugin validate .                   # marketplace + plugin manifests
```

## Credits and license

Cohorte re-implements the pipeline of [MiroFish](https://github.com/666ghj/MiroFish) (AGPL-3.0), whose simulation
layer is [OASIS](https://github.com/camel-ai/oasis) by CAMEL-AI (Apache-2.0). Its prompts and several algorithms
(persona fields, configuration heuristics, agent activation, report method) are adapted from MiroFish. Keep this
in mind when choosing a license for this repository.

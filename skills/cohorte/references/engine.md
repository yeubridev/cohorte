# Engine reference

## Files of a simulation (`.cohorte/sims/<name>/`)

| File | Written by | Content |
|---|---|---|
| `meta.json` | init / llm | name, title, parent (fork), status, `llm` {backend, model, concurrency, fallbacks} |
| `world.json`, `seed.md` | init | your stage-1 output, normalised |
| `agents.json` | setup | one profile per agent (editable) |
| `config.json` | setup | time, events, activity, platform and turn settings (editable) |
| `state.json` | run | platforms' posts, comments, follows, mutes, memories, notifications; `round` = next round |
| `turns.jsonl` | run | one record per agent turn: thought, sentiment, probes, actions with context |
| `interviews.jsonl` | interview | questions and answers |
| `llm_cache.jsonl` | all | content-addressed cache of every LLM answer (resume, deterministic replays) |
| `usage.json` | all | calls, tokens, estimated cost by kind |
| `pending_requests.json` / `responses.json` | manual backend | requests for you / your answers |
| `report.md` | you | the prediction report |

## `agents.json` (per agent)
`agent_id`, `name`, `username`, `entity_id`, `entity_name`, `entity_type`, `kind` (individual|group), `bio`,
`persona` (the core prompt of the agent), `age`, `gender`, `mbti`, `country`, `profession`, `interested_topics`
(feed relevance), `sketch` (crowd member seed), `followers` (audience size shown to the agent), `following`
(initial follow graph, agent ids). Editing a persona before `run` is the right way to fix a miscast agent.

## `config.json`
- `time_config`: `total_simulation_hours`, `minutes_per_round`, `total_rounds` (recomputed only by setup; keep
  consistent if you edit), `start_hour`, `agents_per_hour_min/max` (agents activated per round before
  multipliers), `peak_hours` ×1.5, `off_peak_hours` ×0.05, `morning_hours` ×0.4, `work_hours` ×0.7, `timezone`.
- `agent_configs[]`: `activity_level` (0-1, chance to be a candidate when in an active hour), `active_hours`,
  `posts_per_hour` / `comments_per_hour` (habits shown to the agent), `response_delay_*` (informational),
  `sentiment_bias` (-1..1) and `stance` (supportive|opposing|neutral|observer) as starting disposition,
  `influence_weight` (feed ranking boost and follower count).
- `event_config`: `hot_topics` (feed relevance), `narrative_direction` (informational), `initial_posts[]`
  {content, poster_agent_id, platforms, source}, `scheduled_events[]` {at_hour|at_round, content,
  poster_agent_id|null for NEWS, platforms, label}.
- `platform_configs.{twitter,reddit}`: `recency_weight`, `popularity_weight`, `relevance_weight`,
  `viral_threshold` (engagement that triggers a boost), `echo_chamber_strength` (boost for authors with similar
  sentiment).
- `probes[]`, `turn` {`max_actions`, `memory_size`, `feed_size`, `effort` (low), `max_tokens`}.

Activation per round (MiroFish `get_active_agents_for_round`): `target = uniform(min, max) × hour multiplier`;
candidates are agents whose `active_hours` contain the hour and who pass an `activity_level` draw; `target` of
them are sampled. Draws use a seeded RNG per (seed, platform, round), so `estimate` is exact and forks share the
same schedule.

## What an agent sees each turn
System: platform rules + action list + JSON contract (shared by all agents of a platform), then the agent's
persona block (identity, bio, persona, habits, stance, follower count). User: simulated clock, its last
`memory_size` memories (own actions and thoughts), notifications (likes, replies, quotes, mentions, follows since
last time), and its ranked feed (recency, popularity, relevance to its interests, follows, echo chamber,
author influence, a penalty for already-seen posts, one exploration slot; Reddit shows top comments).
It answers `{"thought", "sentiment", "probes", "actions": [1-3]}`. Reddit-style `SEARCH_POSTS`, `SEARCH_USER`,
`TREND` and `REFRESH` return results and give the agent one follow-up decision.

Actions: Twitter-like = CREATE_POST, LIKE_POST, REPOST, QUOTE_POST, FOLLOW, DO_NOTHING (MiroFish's set).
Reddit-like = CREATE_POST, CREATE_COMMENT, LIKE/DISLIKE_POST, LIKE/DISLIKE_COMMENT, SEARCH_POSTS, SEARCH_USER,
TREND, REFRESH, FOLLOW, MUTE, DO_NOTHING.

## Crowd tier (large populations)
- **Setup**: one LLM call per crowd designs `archetypes_per_crowd` archetypes (label, share, description, voice,
  interests, professions, countries, ages, mood mean/spread, activity, susceptibility, stance). Members are
  sampled from them (names from built-in multicultural pools; `agents.json` entries have `"tier": "crowd"`,
  `archetype`); `config.crowd` holds the archetypes, segments, `hourly_rate`, `feed_size`, `max_actions`.
- **Activation**: each member is online with probability `hourly_rate × minutes_per_round/60 × 2·activity ×
  hour multiplier` when the hour is in its active hours (seeded, so `estimate` stays exact).
- **Decision**: per platform and round, one `crowd_turn` call per segment with online members shows the archetypes
  (with their current mood and last collective thoughts) and the 10 posts most seen by those members. It returns per
  archetype: reaction probabilities per post (`like`, `repost`/`dislike`, `comment`), 1-3 comment and post texts,
  `new_post_rate`, `sentiment`, `probes` and a collective `thought`.
- **Sampling**: each member walks its own feed: comment (uses one of the archetype's texts, each text once),
  dislike (more likely when it disagrees with the author), like (more likely when it agrees; may follow the
  author), repost; maybe writes one of the archetype's posts. Opinion moves toward the archetype mood plus the
  member's own offset, scaled by susceptibility; probes = archetype answer + member offset + noise.
- **Effects on LLM agents**: crowd reactions change counts, rankings and virality in everyone's feeds, crowd
  comments appear in threads, and authors get one aggregated notification per post ("212 people liked…").
- **Records**: `turns.jsonl` gets one compact record per member turn (`source: "crowd"`) and one per archetype and
  round (`source: "crowd_summary"`, with the collective thought). `stats`, `timeline`, `search --thoughts`,
  `agent`, `thread` and `interview` all handle crowd members.

## Performance
Feeds are ranked from a per-round pool of the 150 hottest posts plus the latest posts of followed accounts, with
cached post keywords and incremental follower counts; `state.json` is written compactly. Measured on the mock
backend: 2,000 all-LLM agents × 6 rounds went from 68 s to 17 s of engine time; 10,000 agents (18 LLM + 9,982
crowd) × 24 rounds take ~29 s and 353 LLM calls instead of ~32,000.

## Tuning
- Livelier conversation: raise `agents_per_hour_max`, individuals' `activity_level`, or `max_actions`.
- Cheaper: Haiku, fewer rounds (`minutes_per_round` 90-120), lower `persona_words`, one platform, smaller feeds.
- More realistic silence: lower `activity_level` for institutions; lurkers with activity 0.2-0.4.
- Stronger filter bubbles: raise `echo_chamber_strength`; more virality: lower `viral_threshold`.
Change config before `run` (or between chunks; changes apply from the next round).

## Backends in detail
- `anthropic`: `anthropic.Anthropic()` (credentials from `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or an
  `ant auth login` profile), `output_config.effort` low for turns / medium for personas and interviews, the two
  system blocks marked for prompt caching, server-side refusal fallbacks (`fallbacks: "default"`) on models that
  support them (disable with `--no-fallbacks`). SDK retries 429/5xx; a round with more than half its calls failing
  is not committed and can simply be re-run.
- `claude-cli`: `claude -p --output-format json --system-prompt ... --tools "" --no-session-persistence` run from a
  temp directory (so the project's CLAUDE.md does not leak into agents). Measured: 10 agents × 4 rounds × 2
  platforms ≈ 67 calls, ~2 min with concurrency 6, ≈ $0.06 on Haiku 5.5.
- `manual`: each command that needs the LLM writes `pending_requests.json` ({id, kind, expect_json, system,
  user}) and exits with code 3. Write `responses.json` as `{"<id>": "<raw answer>"}` (for JSON requests: exactly
  the JSON object the prompt asks for) and re-run. Valid answers are cached, invalid ones stay pending.
- `mock`: deterministic synthetic answers (tests, demos of the mechanics).

## Differences from MiroFish (by design)
- No Zep/GraphRAG service: the world graph is `world.json`; per-entity context comes from its facts, relations and
  matching seed passages; the "memory graph" is the agents' memories plus `turns.jsonl`.
- Private `thought`, `sentiment` and `probes` on every turn (not in MiroFish) to measure reactions.
- Crowd archetypes (`count`) expanded into individuals, for testing products with populations rather than
  named actors.
- Morning/work multipliers are applied (MiroFish generates them but its runner only applies peak/off-peak).
- `fork` + `compare` for A/B testing on the same cohort.
- The ReportAgent's ReACT loop is Claude itself using the CLI tools.

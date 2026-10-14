# Stage 1 — building the world (`seed.md` + `world.json`)

In MiroFish this stage is an LLM ontology designer plus GraphRAG extraction (Zep). Here you do it by reading the
user's material. The quality of the whole simulation depends on it: agents only know what the seed and their
persona tell them.

## 1. `seed.md` — what the world knows

Write it as the public would encounter it, then add background facts:

```markdown
# <Product / event>
## What people see
<the exact announcement, landing-page copy, pricing table, FAQ, app-store text, screenshots described in words>
## Context
<market, competitors and their prices, recent news, community norms, known controversies>
## Facts about the actors
<one paragraph per important entity; mention entity names verbatim so they get matched to personas>
```

- Copy wording verbatim when the wording is what is being tested.
- Keep internal-only facts (roadmap, costs) out unless the public could know them, or label them as such
  and give them only to the entity that would know (`facts` of that entity in `world.json`).
- 1-10 pages is plenty. Long seeds are excerpted per entity (3,000 characters around their name).

## 2. `world.json` — schema

```jsonc
{
  "title": "Short name of the scenario",
  "subject": "What agents rate their sentiment about, e.g. 'Tandem and its launch'",
  "simulation_requirement": "The prediction question (see SKILL.md step 1)",
  "settings": {
    "platforms": ["twitter", "reddit"],  // one or both
    "language": "English",               // language agents write in
    "total_hours": 48,                   // omit to let setup decide (24-168)
    "minutes_per_round": 60,             // omit to let setup decide (30-120)
    "start_hour": 9,                     // simulated clock at round 0
    "persona_words": 350,                // persona length (MiroFish uses ~2000 chars; longer = costlier turns)
    "initial_posts": 5,                  // generated kick-off posts (max 2 when you give explicit ones)
    "max_actions_per_turn": 3, "memory_size": 12,
    "feed_size": {"twitter": 8, "reddit": 6},
    "seed": 42,                          // random seed: same seed = same activation schedule
    "crowd_threshold": 12,               // crowds above this size use the crowd tier (unless "tier": "llm")
    "delegates": 3,                      // LLM members kept per crowd (interviewable voices)
    "archetypes_per_crowd": 6,           // sub-groups the LLM designs per crowd segment
    "crowd_hourly_rate": 0.15,           // chance an average crowd member is online in a given hour
    "max_agents": 20000, "max_llm_agents": 150
  },
  "ontology": {
    "entity_types": [ {"name": "PascalCase", "kind": "individual|group", "description": "...",
                       "attributes": [{"name": "snake_case", "description": "..."}], "examples": ["..."]} ],
    "edge_types":   [ {"name": "UPPER_SNAKE", "description": "...", "source_targets": [{"source": "T1", "target": "T2"}]} ]
  },
  "entities": [
    {"id": "acme", "name": "Acme", "type": "Startup", "summary": "...", "facts": ["..."], "attributes": {}},
    {"id": "devs", "name": "Indie SaaS developer", "type": "IndieDeveloper", "summary": "...",
     "count": 6, "diversity": "2 bootstrappers, 1 agency, mixed countries; at least 2 skeptical of AI"},
    {"id": "users", "name": "Freelance designer", "type": "Freelancer", "summary": "...",
     "count": 4000, "tier": "auto", "delegates": 3, "diversity": "..."}   // crowd tier: 3 LLM delegates + 3,997 crowd members
  ],
  "relations": [ {"source": "acme", "target": "devs", "type": "SELLS_TO", "fact": "..."} ],
  "initial_posts": [ {"poster": "acme", "content": "exact launch post", "platforms": "all"} ],
  "scheduled_events": [ {"at_hour": 30, "poster": null, "label": "bad review", "content": "..."} ],
  "probes": [ {"key": "try_intent", "question": "How likely are you to try Acme this week?", "min": 0, "max": 10} ]
}
```

`$COHORTE validate world.json` checks references, counts and types and prints warnings.

## 3. Ontology rules (from MiroFish's ontology designer)

- **Entities are actors who can speak on social media**: people, companies (official accounts),
  organisations, institutions, media outlets, communities. Never abstract concepts ("public opinion"), topics
  ("privacy") or stances ("supporters").
- **About 10 entity types**: ~8 specific types drawn from the material (`Nutritionist`, `IndieDeveloper`,
  `MediaOutlet`…) plus the two **fallbacks last**: `Person` (any individual not covered) and `Organization`
  (any organisation not covered). Each specific type's description says how it differs from the fallback.
- Mark each type `"kind": "group"` (one institutional account, voice of an organisation) or `"individual"`.
  Groups get institutional personas (official tone, office hours, high influence); individuals get personal ones.
- 6-10 edge types reflecting real ties (`WORKS_FOR`, `REPORTS_ON`, `COMPETES_WITH`, `SUPPORTS`, `OPPOSES`,
  `FOLLOWS`…). Relations seed the follow graph: related agents follow each other.
- 1-3 attributes per type, snake_case; avoid `name`, `uuid`, `summary`, `created_at` as attribute names.

## 4. Casting the cohort (the part that decides what you learn)

A crowd entity (`count > 1`) is expanded at `setup` into that many distinct individuals (names, ages, countries,
angles) before personas are written. Guidelines:

- **15-40 LLM agents** is the useful range for the voices (named actors + delegates). Under 10, conversations
  stay thin; more mostly adds cost.
- **Crowds** add volume cheaply: 500-10,000 members per segment cost the same number of LLM calls (one per
  segment per round). Use them when the question is about scale effects: what share of a segment reacts, what
  goes viral, how the majority's mood shifts, whether a vocal minority dominates.
- Cover: core target segments (most agents), adjacent or reluctant segments, **at least one hostile or skeptical
  voice per key risk** (price, privacy, ethics, quality), amplifiers (1-2 media, 1-2 creators/experts),
  the user's own organisation (official account), and competitors when relevant.
- Weight crowds by how much their reaction matters to the decision, not by real population share; say so in the
  report.
- Use `diversity` to force spread (budgets, expertise, countries, temperament, prior opinion). Do not stack the
  deck: if every agent is set up to love or hate the product, the run only echoes your casting.
- Specific named entities (a known journalist, a competitor) need a `summary` and `facts` grounded in the seed.
  For real people, prefer a fictional stand-in unless the user explicitly wants a public figure simulated.

## 5. What is being tested goes in `initial_posts`

Put the exact copy under test as kick-off posts, posted by the right entity (usually the user's organisation, plus
optionally a partner or creator). `setup` adds up to 2 generated reaction posts (media, early users). For an A/B
test, keep everything identical and change only these posts (`fork --posts`) or the seed.

## 6. Probes — numbers tied to the decision

Every active agent privately answers each probe on every turn (not visible to other agents). Use 1-3 probes:
- adoption: "How likely are you to sign up / try / buy … this week?" (0-10)
- price: "How acceptable is <price> to you?" (0-10)
- trust: "How much do you trust <org> with your data?" (0-10)
- advocacy: "How likely are you to recommend … to a friend?" (0-10)
Each agent also reports `sentiment` (-1..1) about `subject` every turn.

## 7. God's-eye events (`scheduled_events` or `inject`)

Variables injected at a simulated hour, as a post by an agent or as a `NEWS` item boosted into every feed:
competitor response, price change, outage, critical review, leaked policy clause, influencer endorsement, the
user's own clarification post (tests a crisis response). Use them for "what if" questions; run a control without
the event (`fork`) to measure its effect.

## 8. Recipes

| Scenario | Platforms | Duration | Cast highlights | Probes / events |
|---|---|---|---|---|
| Product launch | both | 48-72 h | target segments ×4-8, skeptics, 2 media, 1-2 creators, competitor, your company | try intent, price; event: competitor reply at 24 h |
| Pricing change | both | 72 h | existing users (loyal, price-sensitive, power users), prospects, competitor | churn intent, fairness; event: your FAQ clarification |
| Landing-page / message A/B | twitter | 24-48 h | cold audience crowds, a few experts | click/try intent; fork with the variant copy |
| Feature removal / ToS change | reddit (+twitter) | 72-120 h | power users, moderators, privacy advocates, media | trust, churn; event: official apology post |
| Open-source / dev tool launch | reddit | 48 h | maintainers, enterprise devs, hobbyists, skeptics of the category | adoption, star intent |
| Internal announcement / policy | reddit | 48-96 h | departments as crowds, managers, union/representatives | support, attrition risk |
| Story / fiction ending | both | 72-168 h | characters as individuals, fan communities | character-specific probes |

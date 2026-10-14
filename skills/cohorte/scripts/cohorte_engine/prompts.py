"""Prompt templates.

Adapted (translated and generalised) from MiroFish's prompts:
  - persona generation   <- backend/app/services/oasis_profile_generator.py
  - time / event / agent <- backend/app/services/simulation_config_generator.py
  - agent turns          <- OASIS social agents (camel-ai/oasis) as driven by MiroFish
  - interviews           <- backend/app/api/simulation.py (INTERVIEW_PROMPT_PREFIX)
"""

from __future__ import annotations


def language_rule(language: str) -> str:
    return (
        f"Write every natural-language value (posts, comments, bios, personas, thoughts, answers) in {language}. "
        "Keep JSON keys, enum values, entity type names and gender values in English."
    )


# --------------------------------------------------------------------------- #
# Environment setup (stage 2)
# --------------------------------------------------------------------------- #

PERSONA_SYSTEM = (
    "You are an expert in social-media user profiling. You write detailed, realistic personas for a "
    "public-opinion simulation, staying as faithful as possible to the known facts about the entity. "
    "Return one valid JSON object; string values must not contain raw line breaks."
)

CROWD_SKETCH_PROMPT = """We are populating a simulated social network. The archetype below stands for a crowd of real people.
Invent exactly {count} distinct individuals who belong to it.

Archetype: {name}
Entity type: {entity_type}
Description: {summary}
Diversity requirements: {diversity}

Simulation context:
{context}

Make them genuinely different (age, gender, location, income, temperament, prior opinion, how online they are).
Spread their initial attitude: some enthusiastic, some skeptical, some indifferent, unless the description says otherwise.
Return JSON:
{{"people": [{{"name": "full realistic name", "age": 34, "gender": "female", "country": "...", "profession": "...", "angle": "one sentence: what makes this person's view of the topic specific"}}]}}"""

PERSONA_INDIVIDUAL_PROMPT = """Create a detailed social-media persona for this entity, staying as close as possible to the known facts.

Entity name: {name}
Entity type: {entity_type}
Entity summary: {summary}
Entity attributes: {attributes}
{sketch}
Context (facts, relations, seed material):
{context}

Return JSON with these fields:
1. bio: social-media bio, max 200 characters
2. persona: a detailed persona description ({words} words, one continuous paragraph, no line breaks) covering:
   - basic info (age, occupation, education, location)
   - background (key experiences, link to the event/topic, social relations)
   - personality (MBTI, core traits, how emotions are expressed)
   - social-media behaviour (posting frequency, content preferences, interaction style, language quirks)
   - stance (attitude toward the topic, what would anger or move them)
   - distinctive traits (catchphrases, hobbies, particular experiences)
   - personal memory (how this individual relates to the event and what they have already done or said about it)
3. age: integer
4. gender: "male", "female" or "nonbinary"
5. mbti: MBTI type (e.g. INTJ, ENFP)
6. country: country name
7. profession: occupation
8. interested_topics: array of 3-6 topics

Rules: values are strings or numbers, no line breaks inside strings, content consistent with the entity information.
{language_rule}"""

PERSONA_GROUP_PROMPT = """Create a detailed social-media account profile for this institution / group entity, staying as close as possible to the known facts.

Entity name: {name}
Entity type: {entity_type}
Entity summary: {summary}
Entity attributes: {attributes}

Context (facts, relations, seed material):
{context}

Return JSON with these fields:
1. bio: official account bio, max 200 characters, professional
2. persona: a detailed account description ({words} words, one continuous paragraph, no line breaks) covering:
   - institution basics (official name, nature, background, main functions)
   - account positioning (account type, target audience, purpose)
   - voice (language style, usual expressions, taboo topics)
   - publishing habits (content types, frequency, active hours)
   - stance (official position on the core topic, how controversy is handled)
   - specifics (who the account represents, how it is run)
   - institutional memory (how it relates to the event and what it has already done or said about it)
3. age: 30 (fixed virtual age for institutional accounts)
4. gender: "other"
5. mbti: an MBTI type describing the account style (e.g. ISTJ = rigorous and conservative)
6. country: country name
7. profession: the institution's function
8. interested_topics: array of 3-6 areas it follows

Rules: no null values, no line breaks inside strings, the account must speak in a way that fits its identity.
{language_rule}"""

TIME_CONFIG_SYSTEM = (
    "You are a social-media simulation expert. Return pure JSON. The time configuration must match the "
    "daily rhythm of the target population in the simulated scenario."
)

TIME_CONFIG_PROMPT = """Generate the time configuration for this simulation.

{context}

## Guidelines (adapt them to the event and the population)
- Infer the time zone and daily rhythm of the people involved. Reference rhythm for a typical population:
  0-5h almost nobody (0.05), 6-8h waking up (0.4), 9-18h working hours (0.7), 19-22h evening peak (1.5), 23h decreasing (0.5).
- Adjust: students peak 21-23h; media are active all day; institutions only during office hours;
  breaking news can keep people up at night (shorten off_peak_hours).
- total_simulation_hours: 24-168. Short for a flash event, longer for a lasting topic. Requested: {requested_hours}.
- minutes_per_round: 30-120 (60 recommended). Requested: {requested_minutes}.
- agents_per_hour_min / agents_per_hour_max: how many agents are activated per round, between 1 and {max_agents}.

Return JSON (no markdown):
{{"total_simulation_hours": 48, "minutes_per_round": 60, "start_hour": 8,
  "agents_per_hour_min": 3, "agents_per_hour_max": 10,
  "peak_hours": [19, 20, 21, 22], "off_peak_hours": [0, 1, 2, 3, 4, 5],
  "morning_hours": [6, 7, 8], "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
  "timezone": "UTC+1", "reasoning": "why"}}"""

EVENT_CONFIG_SYSTEM = (
    "You are a public-opinion analyst. Return pure JSON. poster_type must exactly match one of the "
    "available entity types (English PascalCase)."
)

EVENT_CONFIG_PROMPT = """Generate the event configuration for this simulation.

Simulation requirement: {requirement}

{context}

## Available entity types and examples
{type_info}

## Task
- Extract the hot-topic keywords.
- Describe the likely direction of the public conversation (narrative_direction).
- Write {n_posts} initial posts that kick off the simulation. Each post MUST name a poster_type taken from the
  available entity types so it can be published by a fitting agent (an official statement by an institution,
  news by a media outlet, a first reaction by an individual, ...). Posts must read like real posts on social media.
{language_rule}

Return JSON (no markdown):
{{"hot_topics": ["keyword", "..."], "narrative_direction": "...",
  "initial_posts": [{{"content": "post text", "poster_type": "EntityType"}}],
  "reasoning": "..."}}"""

AGENT_CONFIG_SYSTEM = (
    "You are a social-media behaviour analyst. Return pure JSON. stance must be one of: supportive, "
    "opposing, neutral, observer."
)

AGENT_CONFIG_PROMPT = """Generate a social-media activity configuration for each agent below.

Simulation requirement: {requirement}
Time zone / rhythm of the population: {timezone}

## Agents
```json
{agents_json}
```

## Reference behaviour (adapt to the scenario)
- Institutions (government, universities, companies' official accounts): activity 0.1-0.3, office hours (9-17),
  slow to respond (60-240 min), high influence (2.5-3.0)
- Media outlets: activity 0.4-0.6, active 8-23h, fast (5-30 min), high influence (2.0-2.5)
- Individuals: activity 0.6-0.9, mostly evenings (18-23h), fast (1-15 min), low influence (0.8-1.2)
- Public figures / experts: activity 0.4-0.6, influence 1.5-2.0
- sentiment_bias (-1..1) and stance should follow each agent's persona and known position.

Return JSON (no markdown):
{{"agent_configs": [{{"agent_id": 0, "activity_level": 0.7, "posts_per_hour": 0.5, "comments_per_hour": 1.2,
  "active_hours": [8, 12, 13, 19, 20, 21, 22], "response_delay_min": 2, "response_delay_max": 20,
  "sentiment_bias": 0.0, "stance": "neutral", "influence_weight": 1.0}}]}}
agent_id values must match the input exactly."""


# --------------------------------------------------------------------------- #
# Simulation turns (stage 3)
# --------------------------------------------------------------------------- #

PLATFORM_DESCRIPTIONS = {
    "twitter": (
        "a Twitter/X-like microblogging network: short public posts, likes, reposts, quote-posts and follows. "
        "Your feed mixes posts from people you follow with recommended posts."
    ),
    "reddit": (
        "a Reddit-like forum: posts with threaded comments, upvotes (like) and downvotes (dislike), search and "
        "trending topics. Your feed is a ranked list of posts with their top comments."
    ),
}

ACTION_DOCS = {
    "CREATE_POST": 'CREATE_POST {"content": "..."} - publish a new post',
    "LIKE_POST": 'LIKE_POST {"post_id": 12} - like / upvote a post',
    "DISLIKE_POST": 'DISLIKE_POST {"post_id": 12} - downvote a post',
    "REPOST": 'REPOST {"post_id": 12} - share a post to your followers without comment',
    "QUOTE_POST": 'QUOTE_POST {"post_id": 12, "content": "..."} - share a post with your own comment',
    "FOLLOW": 'FOLLOW {"user_id": 3} - follow a user',
    "MUTE": 'MUTE {"user_id": 3} - stop seeing a user',
    "CREATE_COMMENT": 'CREATE_COMMENT {"post_id": 12, "content": "..."} - reply under a post',
    "LIKE_COMMENT": 'LIKE_COMMENT {"comment_id": 40} - upvote a comment',
    "DISLIKE_COMMENT": 'DISLIKE_COMMENT {"comment_id": 40} - downvote a comment',
    "SEARCH_POSTS": 'SEARCH_POSTS {"query": "..."} - search posts (you will see results, then act again)',
    "SEARCH_USER": 'SEARCH_USER {"query": "..."} - search users (you will see results, then act again)',
    "TREND": "TREND {} - look at trending posts (you will see them, then act again)",
    "REFRESH": "REFRESH {} - reload your feed (you will see it, then act again)",
    "DO_NOTHING": "DO_NOTHING {} - scroll past / stay silent (realistic and common)",
}

TURN_RULES = """You are a real person (or the person running an organisation's account) using {platform_desc}

How to behave:
- Stay strictly in character: your persona decides what you notice, what you care about, your tone, length,
  vocabulary, spelling habits and how often you speak. Most people mostly read, like and scroll; few post.
- React to what is actually in your feed and notifications. Reference posts by their ids.
- You do not know that this is a simulation and you never mention AI, agents or simulations.
- Do not repeat yourself: check your own recent activity first.
- Posts and comments must sound like real social-media writing, not like an essay or a press release
  (unless that is who you are).
{language_rule}

Each time you are online you return ONE JSON object:
{{"thought": "private 1-2 sentence reflection, never shown to anyone",
  "sentiment": <number from -1 (very negative) to 1 (very positive): your current feeling about {subject}>,
{probe_schema}  "actions": [<1 to {max_actions} actions>]}}
Each action is {{"type": "<ACTION_TYPE>", ...arguments}}. Available actions:
{action_docs}"""

PERSONA_BLOCK = """## Who you are
Name: {name} (@{username}), user_id {user_id}
{identity}
Bio: {bio}

{persona}

## Your habits and disposition
- Usually online around: {active_hours}
- Typical activity when online: ~{posts_per_hour} posts and ~{comments_per_hour} replies/comments per hour
- Initial stance on the topic: {stance}; emotional baseline: {sentiment_label}
- Your reach: {followers} followers"""

TURN_USER = """Now: {clock} on {platform_name}.

## Your recent activity (your memory)
{memory}

## Notifications since you were last online
{notifications}

{trending}## Your feed
{feed}

Decide what you realistically do right now. Return only the JSON object."""

FOLLOWUP_USER = """{result_title}
{results}

You are still online. Decide what you do next (searching again is not available now).
Return only the JSON object with "thought", "sentiment"{probe_keys} and "actions"."""

INTERVIEW_SYSTEM_SUFFIX = """You are being interviewed. Stay fully in character as {name}: same knowledge, opinions, tone and
language habits. Answer from your persona, your memories and everything you did and saw on social media.
You do not know you are a simulated persona. Answer in {language}, in plain text (no JSON), like you would
actually talk to an interviewer."""

INTERVIEW_USER = """## Your memories of what happened online
{memory}

{history}Interviewer: Drawing on your persona and all your past memories and actions, answer directly in text, without using any tools: {question}"""


def probe_schema(probes) -> str:
    lines = []
    for p in probes or []:
        lines.append(
            f'  "{p["key"]}": <number {p.get("min", 0)}-{p.get("max", 10)}: {p["question"]}>,\n'
        )
    if lines:
        return '  "probes": {\n' + "".join("  " + line for line in lines).rstrip(",\n") + "\n  },\n"
    return ""

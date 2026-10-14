"""Simulation directory layout and world.json validation (stage 1 output)."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .llm import BACKENDS, DEFAULT_MODEL, resolve_model
from .util import CohorteError, read_json, read_jsonl, read_text, slugify, to_int, write_json

PLATFORMS = ("twitter", "reddit")
GROUP_HINTS = (
    "organization", "organisation", "company", "agency", "media", "outlet", "university", "school",
    "government", "ministry", "brand", "community", "association", "union", "ngo", "institution",
    "platform", "competitor", "group", "publisher", "newspaper", "hospital", "startup", "firm", "fund",
)
DEFAULT_SETTINGS = {
    "platforms": ["twitter", "reddit"],
    "language": "English",
    "total_hours": None,          # None -> let setup decide (LLM), 24-168
    "minutes_per_round": None,    # None -> let setup decide (LLM), 30-120
    "start_hour": None,         # None -> let setup decide (default 8:00)
    "seed": 42,
    "persona_words": 350,
    "initial_posts": 5,           # generated kick-off posts (in addition to explicit ones)
    "max_actions_per_turn": 3,
    "memory_size": 12,
    "feed_size": {"twitter": 8, "reddit": 6},
    # Population tiers: crowds larger than crowd_threshold keep `delegates` full LLM members and
    # turn the rest into crowd-tier agents driven by one LLM call per segment and round.
    "crowd_threshold": 12,
    "delegates": 3,
    "archetypes_per_crowd": 6,
    "crowd_hourly_rate": 0.15,    # chance an average crowd member checks a platform in a given hour
    "max_agents": 20000,
    "max_llm_agents": 150,        # agents with their own LLM call per turn (named actors + delegates)
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Sim:
    """A simulation lives in <home>/sims/<name>/."""

    def __init__(self, home: str, name: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name or ""):
            raise CohorteError(f"Invalid simulation name '{name}' (letters, digits, '-', '_', '.').")
        self.home = home
        self.name = name
        self.dir = os.path.join(home, "sims", name)

    def path(self, *parts: str) -> str:
        return os.path.join(self.dir, *parts)

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path("meta.json"))

    def require(self, *files: str) -> None:
        if not self.exists:
            raise CohorteError(f"No simulation '{self.name}' in {self.home}. Run `init` first (or pass --home).")
        for f in files:
            if not os.path.exists(self.path(f)):
                hint = {"agents.json": "setup", "config.json": "setup", "state.json": "run"}.get(f, "init")
                raise CohorteError(f"{f} is missing for '{self.name}'. Run `{hint}` first.")

    # convenience loaders
    def meta(self) -> Dict[str, Any]:
        return read_json(self.path("meta.json"))

    def save_meta(self, meta: Dict[str, Any]) -> None:
        write_json(self.path("meta.json"), meta)

    def world(self) -> Dict[str, Any]:
        return read_json(self.path("world.json"))

    def seed_text(self) -> str:
        p = self.path("seed.md")
        return read_text(p) if os.path.exists(p) else ""

    def agents(self) -> List[Dict[str, Any]]:
        return read_json(self.path("agents.json"))

    def config(self) -> Dict[str, Any]:
        return read_json(self.path("config.json"))

    def turns(self):
        return read_jsonl(self.path("turns.jsonl"))

    def llm_settings(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        settings = dict(self.meta().get("llm", {}))
        for k, v in (overrides or {}).items():
            if v is not None:
                settings[k] = v
        return settings


def list_sims(home: str) -> List[str]:
    root = os.path.join(home, "sims")
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "meta.json")))


# --------------------------------------------------------------------------- #
# world.json validation
# --------------------------------------------------------------------------- #

def guess_kind(entity_type: str, ontology_kinds: Dict[str, str]) -> str:
    if entity_type in ontology_kinds:
        return ontology_kinds[entity_type]
    low = entity_type.lower()
    if low == "person":
        return "individual"
    return "group" if any(h in low for h in GROUP_HINTS) else "individual"


def validate_world(world: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Normalise world.json in place and return (world, warnings). Raises on fatal problems."""
    warnings: List[str] = []
    if not isinstance(world, dict):
        raise CohorteError("world.json must be a JSON object.")
    req = (world.get("simulation_requirement") or "").strip()
    if not req:
        raise CohorteError("world.json needs a non-empty 'simulation_requirement' (the prediction question).")
    world.setdefault("title", req[:80])
    world.setdefault("subject", world["title"])

    settings = dict(DEFAULT_SETTINGS)
    settings.update({k: v for k, v in (world.get("settings") or {}).items() if v is not None})
    if isinstance(settings.get("platforms"), str):
        settings["platforms"] = [settings["platforms"]]
    bad = [p for p in settings["platforms"] if p not in PLATFORMS]
    if bad or not settings["platforms"]:
        raise CohorteError(f"settings.platforms must be a subset of {list(PLATFORMS)}; got {settings['platforms']}.")
    if "language" in world and "language" not in (world.get("settings") or {}):
        settings["language"] = world["language"]
    world["settings"] = settings

    # Ontology (MiroFish: 10 entity types, the last two being Person / Organization fallbacks).
    onto = world.setdefault("ontology", {})
    etypes = onto.setdefault("entity_types", [])
    onto.setdefault("edge_types", [])
    kinds: Dict[str, str] = {}
    for et in etypes:
        if not isinstance(et, dict) or not et.get("name"):
            raise CohorteError("Each ontology.entity_types item needs a 'name'.")
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", et["name"]):
            warnings.append(f"Entity type '{et['name']}' should be PascalCase.")
        kind = et.get("kind") or guess_kind(et["name"], {})
        et["kind"] = "group" if kind == "group" else "individual"
        kinds[et["name"]] = et["kind"]
    type_names = set(kinds)
    if etypes:
        for fallback in ("Person", "Organization"):
            if fallback not in type_names:
                warnings.append(f"Ontology has no '{fallback}' fallback type (MiroFish always keeps Person and Organization).")
        if not 2 <= len(etypes) <= 12:
            warnings.append(f"Ontology has {len(etypes)} entity types; 6-10 is the sweet spot.")

    # Entities
    entities = world.get("entities")
    if not isinstance(entities, list) or not entities:
        raise CohorteError("world.json needs a non-empty 'entities' list.")
    seen_ids = set()
    total_agents = llm_agents = 0
    for i, ent in enumerate(entities):
        if not isinstance(ent, dict) or not ent.get("name"):
            raise CohorteError(f"entities[{i}] needs at least a 'name'.")
        ent["id"] = str(ent.get("id") or f"e{i + 1}")
        if ent["id"] in seen_ids:
            raise CohorteError(f"Duplicate entity id '{ent['id']}'.")
        seen_ids.add(ent["id"])
        etype = ent.get("type") or "Person"
        if type_names and etype not in type_names:
            fallback = "Organization" if guess_kind(etype, {}) == "group" else "Person"
            warnings.append(f"Entity '{ent['name']}' has type '{etype}' not in the ontology; using '{fallback}'.")
            etype = fallback
        ent["type"] = etype
        ent["kind"] = ent.get("kind") if ent.get("kind") in ("individual", "group") else guess_kind(etype, kinds)
        ent["count"] = max(1, to_int(ent.get("count"), 1) or 1)
        if ent["kind"] == "group" and ent["count"] > 1:
            warnings.append(f"Group entity '{ent['name']}' has count>1; groups are single accounts, count reset to 1.")
            ent["count"] = 1
        ent.setdefault("summary", "")
        ent.setdefault("attributes", {})
        ent.setdefault("facts", [])
        tier = ent.get("tier") or "auto"
        if tier not in ("auto", "llm", "crowd"):
            raise CohorteError(f"Entity '{ent['name']}': tier must be auto, llm or crowd.")
        if ent["count"] == 1 or tier == "llm" or (tier == "auto" and ent["count"] <= int(settings["crowd_threshold"])):
            ent["tier"], ent["llm_members"] = "llm", ent["count"]
        else:
            ent["tier"] = "crowd"
            ent["llm_members"] = min(ent["count"], max(0, to_int(ent.get("delegates"), settings["delegates"])))
        total_agents += ent["count"]
        llm_agents += ent["llm_members"]
    max_agents = int(settings["max_agents"])
    if total_agents > max_agents:
        raise CohorteError(
            f"The world expands to {total_agents} agents (max {max_agents}). Lower some 'count' values "
            "or raise settings.max_agents."
        )
    if llm_agents > int(settings["max_llm_agents"]):
        raise CohorteError(
            f"{llm_agents} agents would each get their own LLM call per turn (max {settings['max_llm_agents']}). "
            "Use tier 'auto'/'crowd' for big crowds (fewer delegates) or raise settings.max_llm_agents "
            "(cost and time grow linearly with LLM agents)."
        )
    world["population"] = {"agents": total_agents, "llm_agents": llm_agents, "crowd_agents": total_agents - llm_agents}
    if total_agents < 4:
        warnings.append(f"Only {total_agents} agents: interactions will be thin (10-40 is typical).")

    by_ref = {e["id"]: e for e in entities}
    by_ref.update({e["name"].lower(): e for e in entities})

    def resolve(ref: Any, where: str) -> Optional[str]:
        if ref is None or ref == "":
            return None
        ent = by_ref.get(str(ref)) or by_ref.get(str(ref).lower())
        if not ent:
            raise CohorteError(f"{where}: unknown entity '{ref}' (use an entity id or exact name).")
        return ent["id"]

    rels = world.setdefault("relations", [])
    for i, rel in enumerate(rels):
        rel["source"] = resolve(rel.get("source"), f"relations[{i}].source")
        rel["target"] = resolve(rel.get("target"), f"relations[{i}].target")
        rel.setdefault("type", "RELATED_TO")
        rel.setdefault("fact", "")

    for key in ("initial_posts", "scheduled_events"):
        items = world.setdefault(key, [])
        for i, post in enumerate(items):
            if not (post.get("content") or "").strip():
                raise CohorteError(f"{key}[{i}] needs 'content'.")
            post["poster"] = resolve(post.get("poster"), f"{key}[{i}].poster")
            plats = post.get("platforms") or post.get("platform") or "all"
            if plats == "all":
                plats = list(settings["platforms"])
            elif isinstance(plats, str):
                plats = [plats]
            post["platforms"] = [p for p in plats if p in settings["platforms"]]
            if key == "scheduled_events" and post.get("at_hour") is None and post.get("at_round") is None:
                raise CohorteError(f"scheduled_events[{i}] needs 'at_hour' (simulated hours from start) or 'at_round'.")

    probes = world.setdefault("probes", [])
    for i, p in enumerate(probes):
        if not p.get("question"):
            raise CohorteError(f"probes[{i}] needs a 'question'.")
        p["key"] = slugify(p.get("key") or p["question"], 24)
        p["min"] = to_int(p.get("min"), 0)
        p["max"] = to_int(p.get("max"), 10)
    if len(probes) > 4:
        warnings.append("More than 4 probes makes every agent turn longer and costlier.")
    return world, warnings


def init_sim(
    sim: Sim,
    world_path: str,
    seed_paths: List[str],
    llm: Dict[str, Any],
    force: bool = False,
    parent: Optional[str] = None,
) -> List[str]:
    if sim.exists and not force:
        raise CohorteError(f"Simulation '{sim.name}' already exists ({sim.dir}). Use --force to overwrite.")
    world, warnings = validate_world(read_json(world_path))
    if force and os.path.isdir(sim.dir):
        shutil.rmtree(sim.dir)
    os.makedirs(sim.dir, exist_ok=True)
    seed_parts = []
    for p in seed_paths:
        if not os.path.exists(p):
            raise CohorteError(f"Seed file not found: {p}")
        seed_parts.append(f"<!-- source: {os.path.basename(p)} -->\n" + read_text(p).strip())
    with open(sim.path("seed.md"), "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(seed_parts))
    if not seed_parts:
        warnings.append("No seed material given: agents will only know what world.json says.")
    write_json(sim.path("world.json"), world)
    backend = llm.get("backend") or "anthropic"
    if backend not in BACKENDS:
        raise CohorteError(f"Unknown backend '{backend}'.")
    sim.save_meta({
        "name": sim.name,
        "title": world["title"],
        "created_at": now_iso(),
        "parent": parent,
        "llm": {
            "backend": backend,
            "model": resolve_model(llm.get("model") or DEFAULT_MODEL),
            "concurrency": int(llm.get("concurrency") or 8),
            "fallbacks": llm.get("fallbacks", True),
        },
        "status": "initialized",
    })
    return warnings

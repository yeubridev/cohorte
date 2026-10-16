"""Offline tests for the cohorte engine (mock and manual backends, no network).

Run: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "cohorte", "scripts")
EXAMPLE = os.path.join(ROOT, "skills", "cohorte", "assets", "world.example.json")
sys.path.insert(0, SCRIPTS)

import cohorte  # noqa: E402
from cohorte_engine.llm import LLMRequest  # noqa: E402
from cohorte_engine.mock import mock_response  # noqa: E402
from cohorte_engine.platform import Platform  # noqa: E402
from cohorte_engine.simulation import parse_decision, select_active  # noqa: E402
from cohorte_engine.util import CohorteError, parse_json_response, rng_for  # noqa: E402
from cohorte_engine.world import validate_world  # noqa: E402
from cohorte_engine.ingest import ingest  # noqa: E402
from cohorte_engine.subjects import default_probes, get_subject, potential  # noqa: E402


def small_world(hours=6):
    with open(EXAMPLE, encoding="utf-8") as f:
        world = json.load(f)
    world["settings"].update({"total_hours": hours, "start_hour": 18})
    world["scheduled_events"][0]["at_hour"] = 2
    return world


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cohorte-test-")
        self.home = os.path.join(self.tmp, "home")
        self.world_path = os.path.join(self.tmp, "world.json")
        with open(self.world_path, "w", encoding="utf-8") as f:
            json.dump(small_world(), f)
        self.seed_path = os.path.join(self.tmp, "seed.md")
        with open(self.seed_path, "w", encoding="utf-8") as f:
            f.write("# Tandem\n\nTandem launches an AI meal planner.\n\nLuca Ferri tested it early.")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, expect=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cohorte.main(["--home", self.home, *args])
        self.assertEqual(code, expect, out.getvalue())
        return out.getvalue()


class TestParsing(unittest.TestCase):
    def test_fenced_and_prose(self):
        self.assertEqual(parse_json_response('Sure!\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_response('blah {"a": [1, 2]} trailing'), {"a": [1, 2]})

    def test_truncated(self):
        self.assertEqual(parse_json_response('{"thought": "hm", "actions": [{"type": "LIKE_POST", "post_id": 3'),
                         {"thought": "hm", "actions": [{"type": "LIKE_POST", "post_id": 3}]})

    def test_raw_newlines_in_strings(self):
        self.assertEqual(parse_json_response('{"persona": "line one\nline two"}')["persona"], "line one\nline two")

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_json_response("no json here")

    def test_decision_normalisation(self):
        probes = [{"key": "try_intent", "question": "q", "min": 0, "max": 10}]
        d = parse_decision(
            '{"thought": "ok", "sentiment": 3, "probes": {"try_intent": 14},'
            ' "actions": [{"action": "like", "args": {"post_id": 2}}, {"type": "BOGUS"}, "DO_NOTHING"]}',
            ["LIKE_POST", "DO_NOTHING"], probes, 3)
        self.assertEqual(d["sentiment"], 1.0)
        self.assertEqual(d["probes"]["try_intent"], 10)
        self.assertEqual(d["actions"], [{"type": "LIKE_POST", "post_id": 2}, {"type": "DO_NOTHING"}])
        self.assertEqual(parse_decision(None, ["DO_NOTHING"], [], 3)["actions"], [{"type": "DO_NOTHING"}])


class TestWorld(unittest.TestCase):
    def test_example_is_valid(self):
        world, warnings = validate_world(small_world())
        self.assertEqual(sum(e["count"] for e in world["entities"]), 23)
        self.assertEqual(world["initial_posts"][0]["poster"], "tandem")
        self.assertEqual(warnings, [])

    def test_errors(self):
        w = small_world()
        del w["simulation_requirement"]
        with self.assertRaises(CohorteError):
            validate_world(w)
        w = small_world()
        w["relations"].append({"source": "nobody", "target": "tandem"})
        with self.assertRaises(CohorteError):
            validate_world(w)
        w = small_world()
        w["entities"][6]["count"] = 50000          # over max_agents
        with self.assertRaises(CohorteError):
            validate_world(w)
        w = small_world()
        w["entities"][6].update({"count": 500, "tier": "llm"})   # 500 LLM agents > max_llm_agents
        with self.assertRaises(CohorteError):
            validate_world(w)

    def test_tiers(self):
        w = small_world()
        w["entities"][6]["count"] = 5000
        world, _ = validate_world(w)
        parents = world["entities"][6]
        self.assertEqual((parents["tier"], parents["llm_members"]), ("crowd", 3))
        self.assertEqual(world["entities"][7]["tier"], "llm")   # 4 members <= crowd_threshold
        self.assertEqual(world["population"]["agents"], 5017)

    def test_unknown_type_falls_back(self):
        w = small_world()
        w["entities"][0]["type"] = "MysteryCompany"
        world, warnings = validate_world(w)
        self.assertEqual(world["entities"][0]["type"], "Organization")
        self.assertTrue(warnings)


class TestPlatform(unittest.TestCase):
    def setUp(self):
        profiles = [{"agent_id": i, "name": f"User {i}", "username": f"user{i}", "entity_type": "Person",
                     "followers": 10, "following": []} for i in range(3)]
        self.p = Platform("reddit", profiles, {"viral_threshold": 5})

    def test_like_rules_and_notifications(self):
        pid = self.p.create_post(0, "hello @user2", 0, "Day 1 10:00")
        self.assertTrue(self.p.notifications[2])  # mention
        self.assertTrue(self.p.apply(1, {"type": "LIKE_POST", "post_id": pid}, 1, "Day 1 11:00")["ok"])
        self.assertFalse(self.p.apply(1, {"type": "LIKE_POST", "post_id": pid}, 1, "Day 1 11:00")["ok"])
        self.assertFalse(self.p.apply(0, {"type": "LIKE_POST", "post_id": pid}, 1, "Day 1 11:00")["ok"])
        rec = self.p.apply(2, {"type": "CREATE_COMMENT", "post_id": pid, "content": "hi"}, 1, "Day 1 11:00")
        self.assertTrue(rec["ok"])
        self.assertTrue(any("commented" in n for n in self.p.notifications[0]))
        self.assertFalse(self.p.apply(1, {"type": "LIKE_POST", "post_id": 999}, 1, "x")["ok"])
        self.assertTrue(self.p.apply(1, {"type": "FOLLOW", "user_id": "@user0"}, 1, "x")["ok"])
        self.assertIn(0, self.p.follows[1])

    def test_round_trip(self):
        self.p.create_post(0, "a post", 0, "c")
        clone = Platform("reddit", list(self.p.profiles.values()), {}, json.loads(json.dumps(self.p.to_dict())))
        self.assertEqual(clone.to_dict(), self.p.to_dict())
        self.assertEqual(clone.next_post, 2)

    def test_activation_is_seeded(self):
        cfg = {"time_config": {"agents_per_hour_min": 1, "agents_per_hour_max": 3, "peak_hours": [20]},
               "agent_configs": [{"agent_id": i, "active_hours": [20], "activity_level": 0.9} for i in range(10)]}
        a = select_active(cfg, 20, rng_for(1, "twitter", 5))
        b = select_active(cfg, 20, rng_for(1, "twitter", 5))
        self.assertEqual(a, b)
        self.assertEqual(select_active(cfg, 3, rng_for(1, "twitter", 5)), [])


class TestPipeline(CliCase):
    def test_full_pipeline_mock(self):
        self.cli("init", "demo", "--world", self.world_path, "--seed", self.seed_path, "--backend", "mock")
        self.cli("setup", "demo")
        show = self.cli("show", "demo")
        self.assertIn("Kick-off posts", show)
        self.assertIn("total ≈", self.cli("estimate", "demo", "--model", "haiku"))
        self.cli("run", "demo")
        stats = self.cli("stats", "demo")
        self.assertIn("rounds 6/6", stats)
        self.assertIn("try_intent", stats)
        self.assertIn("INJECTED EVENT", self.cli("timeline", "demo", "--bucket", "2"))
        self.assertIn("Search", self.cli("search", "demo", "privacy", "--thoughts"))
        self.assertIn("Activity log", self.cli("agent", "demo", "luca"))
        self.assertIn("mock answer", self.cli("interview", "demo", "-q", "Why?", "--type", "BusyParent", "--sample", "2"))
        self.assertIn("[post 1]", self.cli("thread", "demo", "1", "--platform", "twitter"))
        posts = os.path.join(self.tmp, "posts.json")
        with open(posts, "w") as f:
            json.dump([{"poster": "tandem", "content": "Tandem is live: 6 EUR/month, no data sharing."}], f)
        self.cli("fork", "demo", "demo-b", "--posts", posts, "--replace-posts")
        self.cli("inject", "demo-b", "--at-hour", "3", "--content", "Breaking: competitor goes free")
        self.cli("run", "demo-b")
        self.assertIn("sentiment_latest_mean", self.cli("compare", "demo", "demo-b"))
        self.cli("setup", "demo", expect=2)  # already set up

    def test_resume_is_deterministic(self):
        self.cli("init", "full", "--world", self.world_path, "--backend", "mock")
        self.cli("setup", "full")
        self.cli("fork", "full", "chunked")
        self.cli("run", "full")
        self.cli("run", "chunked", "--max-rounds", "2")
        self.cli("run", "chunked", "--max-rounds", "1")
        self.cli("run", "chunked")

        def turns(name):
            with open(os.path.join(self.home, "sims", name, "turns.jsonl")) as f:
                return [json.loads(line) for line in f]
        self.assertEqual(turns("full"), turns("chunked"))

    def test_crowd_scale(self):
        """Thousands of crowd agents: few LLM calls, deterministic, resumable, analysable."""
        import time
        w = small_world(hours=8)
        for ent in w["entities"]:
            if ent["id"] in ("parents", "fitness", "privacy", "general"):
                ent["count"] = 1500
        with open(self.world_path, "w") as f:
            json.dump(w, f)
        self.cli("init", "crowd", "--world", self.world_path, "--backend", "mock")
        self.cli("setup", "crowd")
        self.assertIn("6006 agents", self.cli("show", "crowd"))
        self.assertIn("segment calls", self.cli("estimate", "crowd"))
        self.cli("fork", "crowd", "crowd-chunked")
        start = time.time()
        self.cli("run", "crowd")
        elapsed = time.time() - start
        self.cli("run", "crowd-chunked", "--max-rounds", "3")
        self.cli("run", "crowd-chunked")

        def turns(name):
            with open(os.path.join(self.home, "sims", name, "turns.jsonl")) as f:
                return [json.loads(line) for line in f]
        full = turns("crowd")
        self.assertEqual(full, turns("crowd-chunked"))
        crowd_turns = [t for t in full if t["source"] == "crowd"]
        self.assertGreater(len(crowd_turns), 1000)
        self.assertTrue(any(t["source"] == "crowd_summary" and t["thought"] for t in full))
        with open(os.path.join(self.home, "sims", "crowd", "usage.json")) as f:
            calls = json.load(f)["calls"]
        self.assertLess(calls, len(crowd_turns) / 5)
        self.assertLess(elapsed, 120)
        stats = self.cli("stats", "crowd")
        self.assertIn("Crowd archetypes", stats)
        crowd_id = next(t["agent_id"] for t in crowd_turns)
        self.assertIn("crowd member of", self.cli("agent", "crowd", str(crowd_id)))
        self.assertIn("mock answer", self.cli("interview", "crowd", "-q", "Why?", "--agents", str(crowd_id)))
        self.assertIn("crowd", self.cli("thread", "crowd", "1", "--platform", "twitter"))

    def test_manual_backend_loop(self):
        self.cli("init", "man", "--world", self.world_path, "--backend", "manual")
        sim_dir = os.path.join(self.home, "sims", "man")
        for command in (["setup", "man"], ["run", "man", "--until-round", "2"]):
            for _ in range(20):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = cohorte.main(["--home", self.home, *command])
                if code == 0:
                    break
                self.assertEqual(code, 3, out.getvalue())
                with open(os.path.join(sim_dir, "pending_requests.json")) as f:
                    pending = json.load(f)
                answers = {}
                for r in pending["requests"]:
                    # Rebuild the request the engine expects and answer it like Claude would.
                    req = LLMRequest(id=r["id"], kind=r["kind"], system=[r["system"]], user=r["user"],
                                     meta=_mock_meta(r))
                    answers[r["id"]] = mock_response(req)
                with open(os.path.join(sim_dir, "responses.json"), "w") as f:
                    json.dump(answers, f)
            else:
                self.fail(f"{command} never completed")
        self.assertIn("round 2/", self.cli("status", "man"))


class TestSubjects(CliCase):
    def test_profiles(self):
        self.assertEqual(get_subject("film")["kind"], "movie")
        self.assertEqual(get_subject("unknown-thing")["kind"], "generic")
        dims = get_subject("app")["dimensions"]
        self.assertEqual(potential({d["key"]: d["max"] for d in dims}, dims), 100.0)
        self.assertEqual(potential({d["key"]: d["min"] for d in dims}, dims), 0.0)
        self.assertEqual(len(default_probes("movie")), 2)

    def test_ingest_formats(self):
        import zipfile
        html = os.path.join(self.tmp, "p.html")
        with open(html, "w") as f:
            f.write("<html><head><title>T</title><script>bad()</script></head><body><h1>Big promise</h1>"
                    "<button>Buy now</button>" + "<p>word </p>" * 200 + "</body></html>")
        srt = os.path.join(self.tmp, "t.srt")
        with open(srt, "w") as f:
            f.write("1\n00:00:01,000 --> 00:02:00,000\nhello there\n")
        docx = os.path.join(self.tmp, "d.docx")
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:p><w:t>Docx body text</w:t></w:p></w:document>")
        out = os.path.join(self.tmp, "draft")
        subject = ingest([html, srt, docx], "film", out, title="X")
        self.assertEqual(subject["kind"], "movie")
        self.assertEqual(subject["evidence"], "full")
        seed = open(os.path.join(out, "seed.md")).read()
        self.assertIn("Big promise", seed)
        self.assertIn("Buy now", seed)
        self.assertNotIn("bad()", seed)
        self.assertIn("Duration ≈ 2.0 min", seed)
        self.assertIn("Docx body text", seed)

    def test_assess_confidence_and_calibration(self):
        out = os.path.join(self.tmp, "draft")
        page = os.path.join(self.tmp, "p.md")
        with open(page, "w") as f:
            f.write("# Tandem\n" + "A meal planner that reads your fridge. " * 40)
        ingest([page], "website", out)
        w = small_world()
        w.pop("probes")
        with open(self.world_path, "w") as f:
            json.dump(w, f)
        self.cli("init", "lp", "--world", self.world_path, "--seed", os.path.join(out, "seed.md"),
                 "--subject", os.path.join(out, "subject.json"), "--backend", "mock")
        self.cli("setup", "lp")
        self.assertIn("click_intent", self.cli("show", "lp"))           # default probes from the subject kind
        self.assertIn("LLM calls", self.cli("assess", "lp", "--runs", "2", "--dry-run"))
        text = self.cli("assess", "lp", "--runs", "3", "--models", "opus,haiku")
        self.assertIn("Confidence index", text)
        res = json.load(open(os.path.join(self.home, "sims", "lp", "assessment.json")))
        self.assertLessEqual(res["confidence"]["index"], 60)           # capped until calibrated
        self.assertIsNotNone(res["confidence"]["components"]["model_agreement"])
        # five outcomes that track predictions perfectly lift the cap above 60
        for i in range(5):
            name = f"lp{i}"
            self.cli("fork", "lp", name)
            with open(os.path.join(self.home, "sims", name, "seed.md"), "a") as f:
                f.write(f"\nvariant {i}")
            self.cli("assess", name, "--runs", "2")
            pred = json.load(open(os.path.join(self.home, "sims", name, "assessment.json")))["potential"]["mean"]
            self.cli("calibrate", "add", "--sim", name, "--actual", str(round(pred / 10 * 0.8 + 1, 3)),
                     "--scale", "0", "10", "--metric", "conversion %")
        self.assertIn("r = 1.0", self.cli("calibrate", "fit", "--kind", "website"))
        self.cli("assess", "lp", "--runs", "2")
        res = json.load(open(os.path.join(self.home, "sims", "lp", "assessment.json")))
        self.assertGreater(res["confidence"]["cap"], 60)
        self.assertIsNotNone(res["calibrated"])


def _mock_meta(r):
    """Minimal hints so the mock can answer manual-backend requests."""
    meta = {"name": "Someone", "subject": "Tandem", "kind": "individual", "type": "Person"}
    if r["kind"] == "crowd":
        import re
        meta["count"] = int(re.search(r"exactly (\d+)", r["user"]).group(1))
    if r["kind"] == "agent_config":
        meta["agent_ids"] = [int(x) for x in __import__("re").findall(r'"agent_id": (\d+)', r["user"])]
    if r["kind"] == "event_config":
        meta["types"] = ["Startup", "Person"]
        meta["n_posts"] = 2
    if r["kind"] in ("turn", "followup"):
        meta["actions"] = ["CREATE_POST", "LIKE_POST", "DO_NOTHING"]
    return meta


if __name__ == "__main__":
    unittest.main()

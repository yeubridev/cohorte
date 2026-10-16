#!/usr/bin/env python3
"""cohorte - swarm-intelligence rehearsal engine (MiroFish pipeline as a Claude skill).

Stages
  init      create a simulation from world.json (+ seed material)        [stage 1 output]
  setup     personas + time/event/agent configs                          [stage 2]
  show      roster and configuration summary
  estimate  cost estimate for the remaining rounds
  run       run (or resume) the round-based simulation                   [stage 3]
  inject    add a god's-eye event at a given simulated hour/round
  status | stats | timeline | search | agent | thread | export           [stage 4 tools]
  interview ask simulated agents questions in character                  [stage 5]
  ingest    turn URLs, pages, documents, subtitles, images, videos into seed material
  assess    panel rating of the subject: potential score + confidence index  [any subject kind]
  calibrate record real outcomes and fit them (raises the confidence ceiling)
  backtest  score Cohorte vs a single-prompt baseline on cases with known outcomes (the proof gate)
  kinds     list subject kinds (app, website, video, movie, article, ...)
  fork | compare                                                         [adapt & re-test]
  llm       change backend / model of a simulation
  doctor    check which LLM backends are available

Simulations live in ./.cohorte/sims/<name>/ (override with --home or COHORTE_HOME).
Exit codes: 0 ok, 2 error, 3 manual backend is waiting for responses.json.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cohorte_engine import __version__  # noqa: E402
from cohorte_engine import analysis  # noqa: E402
from cohorte_engine.environment import run_setup  # noqa: E402
from cohorte_engine.llm import BACKENDS, DEFAULT_MODEL, AwaitingResponses, LLMClient, resolve_model  # noqa: E402
from cohorte_engine.simulation import inject_event, run_simulation  # noqa: E402
from cohorte_engine.util import CohorteError, read_json, write_json  # noqa: E402
from cohorte_engine.world import Sim, init_sim, list_sims, validate_world  # noqa: E402
from cohorte_engine import assess as assess_mod  # noqa: E402
from cohorte_engine import backtest as backtest_mod  # noqa: E402
from cohorte_engine import calibration  # noqa: E402
from cohorte_engine.ingest import ingest as ingest_sources  # noqa: E402
from cohorte_engine.subjects import SUBJECTS, get_subject  # noqa: E402


def _home(args) -> str:
    return os.path.abspath(args.home or os.environ.get("COHORTE_HOME") or ".cohorte")


def _sim(args, name=None) -> Sim:
    return Sim(_home(args), name or args.name)


def _llm(sim: Sim, args) -> LLMClient:
    overrides = {
        "backend": getattr(args, "backend", None),
        "model": getattr(args, "model", None),
        "concurrency": getattr(args, "concurrency", None),
    }
    return LLMClient(sim.dir, sim.llm_settings(overrides))


def cmd_doctor(args):
    print(f"cohorte {__version__} · python {sys.version.split()[0]}")
    try:
        import anthropic  # noqa: F401
        sdk = f"installed ({getattr(anthropic, '__version__', '?')})"
    except ImportError:
        sdk = "missing (pip install anthropic)"
    creds = [k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(k)]
    ant = shutil.which("ant")
    claude = shutil.which("claude")
    print(f"- anthropic backend: SDK {sdk}; credentials: {', '.join(creds) or 'no env var'}"
          f"{' (ant CLI found: `ant auth status` may show a profile)' if ant else ''}")
    print(f"- claude-cli backend: {'available: ' + claude if claude else 'claude command not found'}")
    print("- manual backend: always available (Claude answers pending_requests.json itself; slow)")
    print("- mock backend: always available (offline fake data for dry runs)")
    if creds and "missing" not in sdk:
        rec = "anthropic"
    elif claude:
        rec = "claude-cli"
    else:
        rec = "manual"
    print(f"recommended: --backend {rec}")
    print(f"simulations home: {_home(args)} ({len(list_sims(_home(args)))} found)")


def cmd_validate(args):
    world, warnings = validate_world(read_json(args.world))
    n = sum(e["count"] for e in world["entities"])
    print(f"world OK: {len(world['entities'])} entities -> {n} agents, "
          f"{len(world['ontology']['entity_types'])} entity types, {len(world.get('relations', []))} relations, "
          f"platforms {world['settings']['platforms']}, language {world['settings']['language']}")
    for w in warnings:
        print(f"warning: {w}")


def cmd_init(args):
    sim = _sim(args)
    warnings = init_sim(sim, args.world, args.seed or [], {
        "backend": args.backend, "model": args.model, "concurrency": args.concurrency,
        "fallbacks": not args.no_fallbacks,
    }, force=args.force, subject_path=args.subject)
    for w in warnings:
        print(f"warning: {w}")
    meta = sim.meta()
    print(f"created {sim.dir}\nllm: {meta['llm']['backend']} / {meta['llm']['model']}\nnext: setup {sim.name}")


def cmd_llm(args):
    sim = _sim(args)
    sim.require()
    meta = sim.meta()
    if args.backend:
        meta["llm"]["backend"] = args.backend
    if args.model:
        meta["llm"]["model"] = resolve_model(args.model)
    if args.concurrency:
        meta["llm"]["concurrency"] = args.concurrency
    if args.no_fallbacks:
        meta["llm"]["fallbacks"] = False
    sim.save_meta(meta)
    print(f"llm: {meta['llm']}")


def cmd_setup(args):
    sim = _sim(args)
    if os.path.exists(sim.path("agents.json")) and not args.force:
        raise CohorteError("Setup already done (agents.json exists). Use --force to regenerate "
                           "(cached LLM answers are reused when prompts did not change).")
    if os.path.exists(sim.path("state.json")) and args.force:
        raise CohorteError("This simulation has already started; fork it instead of re-running setup.")
    run_setup(sim, _llm(sim, args))
    print(f"next: show {sim.name} · estimate {sim.name} · run {sim.name}")


def cmd_show(args):
    print(analysis.show(_sim(args), full=args.full))


def cmd_estimate(args):
    print(analysis.estimate(_sim(args), args.model))


def cmd_run(args):
    sim = _sim(args)
    run_simulation(sim, _llm(sim, args), max_rounds=args.max_rounds, until_round=args.until_round)
    print(analysis.status(sim))


def cmd_inject(args):
    ev = inject_event(_sim(args), args.content, args.at_hour, args.at_round, args.poster, args.platforms, args.label or "")
    who = f"agent {ev['poster_agent_id']}" if ev["poster_agent_id"] is not None else "NEWS"
    print(f"scheduled at round {ev['round']} by {who} on {', '.join(ev['platforms'])}")


def cmd_status(args):
    if not args.name:
        home = _home(args)
        sims = list_sims(home)
        print(f"{len(sims)} simulation(s) in {home}")
        for s in sims:
            meta = Sim(home, s).meta()
            print(f"- {s}: {meta.get('status')} · {meta.get('title')}")
        return
    print(analysis.status(_sim(args)))


def cmd_stats(args):
    print(analysis.stats(_sim(args), args.platform))


def cmd_timeline(args):
    print(analysis.timeline(_sim(args), args.bucket, args.platform))


def cmd_search(args):
    print(analysis.search(_sim(args), " ".join(args.query), args.limit, args.thoughts, args.platform))


def cmd_agent(args):
    print(analysis.agent(_sim(args), args.ref))


def cmd_thread(args):
    print(analysis.thread(_sim(args), args.post_id, args.platform))


def cmd_export(args):
    text = analysis.export(_sim(args), args.platform)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)


def cmd_interview(args):
    sim = _sim(args)
    refs = [r for chunk in (args.agents or []) for r in chunk.split(",") if r.strip()]
    print(analysis.interview(sim, _llm(sim, args), args.question, refs or None, args.type, args.sample,
                             args.all, args.followup))


def cmd_fork(args):
    src, dst = _sim(args, args.source), _sim(args, args.name)
    src.require("agents.json", "config.json")
    if dst.exists and not args.force:
        raise CohorteError(f"'{dst.name}' already exists (use --force).")
    if os.path.isdir(dst.dir):
        shutil.rmtree(dst.dir)
    os.makedirs(dst.dir)
    for f in ("world.json", "seed.md", "agents.json", "config.json", "subject.json"):
        if os.path.exists(src.path(f)):
            shutil.copy(src.path(f), dst.path(f))
    if args.seed:
        with open(dst.path("seed.md"), "w", encoding="utf-8") as out:
            out.write("\n\n---\n\n".join(open(p, encoding="utf-8").read().strip() for p in args.seed))
    meta = src.meta()
    meta.update({"name": dst.name, "parent": src.name, "status": "ready"})
    if args.title:
        meta["title"] = args.title
    dst.save_meta(meta)
    if args.posts:
        posts = read_json(args.posts)
        cfg = dst.config()
        profiles = dst.agents()
        new_posts = []
        for p in posts:
            poster = p.get("poster")
            aid = analysis.resolve_agent(profiles, poster)["agent_id"] if poster not in (None, "") else None
            new_posts.append({"content": p["content"], "poster_type": None, "poster_agent_id": aid,
                              "platforms": p.get("platforms") or cfg["platforms"], "source": "world"})
        keep = [] if args.replace_posts else [p for p in cfg["event_config"]["initial_posts"] if p.get("source") != "world"]
        cfg["event_config"]["initial_posts"] = new_posts + keep
        write_json(dst.path("config.json"), cfg)
    print(f"forked {src.name} -> {dst.name} (same agents, personas and configs; fresh state)\n"
          f"edit {dst.path('config.json')} (event_config.initial_posts / scheduled_events) if needed, then: run {dst.name}")


def cmd_ingest(args):
    subject = ingest_sources(args.sources, args.kind, args.out, args.title, args.notes)
    print(f"wrote {os.path.join(args.out, 'seed.md')} and subject.json · kind {subject['kind']} · "
          f"evidence {subject['evidence']} · {len(subject['sources'])} source(s)")
    if subject["to_describe"]:
        print("Claude: open these images/frames, describe what a viewer sees, and replace the DESCRIBE markers "
              "in seed.md:")
        for f in subject["to_describe"]:
            print(f"  - {f}")


def cmd_kinds(args):
    for k in sorted(SUBJECTS):
        prof = get_subject(k)
        print(f"- {k}: {', '.join(d['key'] for d in prof['dimensions'])}  (outcomes: {', '.join(prof['outcomes'])})")


def cmd_assess(args):
    sim = _sim(args)
    models = [m for m in (args.models or "").split(",") if m] or None
    settings = sim.llm_settings({"backend": args.backend, "model": args.model, "concurrency": args.concurrency})
    if args.dry_run:
        plan = assess_mod.run_assessment(sim, settings, args.runs, models, dry_run=True)
        print(f"{plan['calls']} LLM calls: {plan['respondents']} respondents × {plan['runs']} run(s) "
              f"over {', '.join(plan['models'])}")
        return
    result = assess_mod.run_assessment(sim, settings, args.runs, models)
    print(assess_mod.render(result))
    print(f"\n(saved to {sim.path('assessment.md')})")


def cmd_calibrate(args):
    home = _home(args)
    if args.action == "add":
        sim = _sim(args, args.sim)
        res = read_json(sim.path("assessment.json"))
        data = calibration.add(home, res["kind"], sim.name, res["potential"]["mean"], args.actual,
                               args.scale[0], args.scale[1], args.metric or "", args.note or "")
        print(f"recorded {sim.name}: predicted {res['potential']['mean']} vs actual {args.actual} "
              f"({len(data['pairs'])} pair(s) for '{res['kind']}')")
    elif args.action == "fit":
        data = calibration.fit(home, get_subject(args.kind)["kind"])
        print(calibration.show(home, data["kind"]))
    else:
        print(calibration.show(home, get_subject(args.kind)["kind"] if args.kind else None))


def cmd_backtest(args):
    cases = backtest_mod.load_cases(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        raise CohorteError("No case selected.")
    plan = backtest_mod.plan(cases, args.runs)
    print(f"{plan['cases']} case(s), {plan['variants']} variant(s): ≈{plan['total_calls']} LLM calls "
          f"(setup {plan['setup_calls']}, cohort ratings {plan['assess_calls']}, baseline {plan['baseline_calls']})")
    if args.dry_run:
        return
    name = os.path.basename(os.path.normpath(args.cases)).rsplit(".", 1)[0]
    out = os.path.abspath(args.out or os.path.join(_home(args), "backtests", name))
    settings = {"backend": args.backend, "model": args.model, "concurrency": args.concurrency}
    models = [m for m in (args.models or "").split(",") if m] or None
    result = backtest_mod.run_backtest(cases, out, settings, args.runs, models, args.budget,
                                       _home(args) if args.calibrate else None)
    print(backtest_mod.render(result, cases))
    print(f"\n(saved to {os.path.join(out, 'scorecard.md')})")


def cmd_compare(args):
    print(analysis.compare(_sim(args, args.a), _sim(args, args.b)))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cohorte", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", help="simulations home (default ./.cohorte or $COHORTE_HOME)")
    sub = ap.add_subparsers(dest="command", required=True)

    def llm_flags(p, defaults=False):
        p.add_argument("--backend", choices=BACKENDS, default="anthropic" if defaults else None)
        p.add_argument("--model", default=DEFAULT_MODEL if defaults else None,
                       help="model id or alias: opus, sonnet, haiku, fable")
        p.add_argument("--concurrency", type=int, default=8 if defaults else None)

    p = sub.add_parser("doctor", help="check available LLM backends")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("validate", help="validate a world.json without creating anything")
    p.add_argument("world")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("init", help="create a simulation from world.json + seed files")
    p.add_argument("name")
    p.add_argument("--world", required=True)
    p.add_argument("--seed", action="append", help="seed material file (repeatable)")
    llm_flags(p, defaults=True)
    p.add_argument("--no-fallbacks", action="store_true", help="disable server-side refusal fallbacks")
    p.add_argument("--subject", help="subject.json written by `ingest` (kind, evidence level)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("llm", help="change the LLM settings of a simulation")
    p.add_argument("name")
    llm_flags(p)
    p.add_argument("--no-fallbacks", action="store_true")
    p.set_defaults(func=cmd_llm)

    p = sub.add_parser("setup", help="stage 2: personas and simulation config")
    p.add_argument("name")
    llm_flags(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("show", help="roster and configuration")
    p.add_argument("name")
    p.add_argument("--full", action="store_true", help="include full personas")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("estimate", help="estimate remaining cost")
    p.add_argument("name")
    p.add_argument("--model")
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("run", help="stage 3: run / resume the simulation")
    p.add_argument("name")
    p.add_argument("--max-rounds", type=int, help="run at most N rounds this time (resume later)")
    p.add_argument("--until-round", type=int, help="stop before this round")
    llm_flags(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("inject", help="schedule a god's-eye event")
    p.add_argument("name")
    p.add_argument("--content", required=True)
    p.add_argument("--at-hour", type=float, help="simulated hours since start")
    p.add_argument("--at-round", type=int)
    p.add_argument("--poster", help="agent id/username/name; omit for a NEWS item")
    p.add_argument("--platforms", nargs="+", choices=["twitter", "reddit"])
    p.add_argument("--label")
    p.set_defaults(func=cmd_inject)

    p = sub.add_parser("status", help="progress and usage (no name: list simulations)")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stats", help="aggregate metrics: activity, sentiment, probes, top posts")
    p.add_argument("name")
    p.add_argument("--platform", choices=["twitter", "reddit"])
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("timeline", help="how the conversation evolved, bucket by bucket")
    p.add_argument("name")
    p.add_argument("--bucket", type=float, default=6, help="hours per bucket (default 6)")
    p.add_argument("--platform", choices=["twitter", "reddit"])
    p.set_defaults(func=cmd_timeline)

    p = sub.add_parser("search", help="keyword search over posts, comments (and private thoughts)")
    p.add_argument("name")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--thoughts", action="store_true", help="include agents' private thoughts (god's-eye view)")
    p.add_argument("--platform", choices=["twitter", "reddit"])
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("agent", help="one agent's persona, config and full activity log")
    p.add_argument("name")
    p.add_argument("ref", help="agent id, @username or name")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("thread", help="a post with its likes, quotes and comments")
    p.add_argument("name")
    p.add_argument("post_id", type=int)
    p.add_argument("--platform", choices=["twitter", "reddit"])
    p.set_defaults(func=cmd_thread)

    p = sub.add_parser("export", help="full transcript as markdown")
    p.add_argument("name")
    p.add_argument("--platform", choices=["twitter", "reddit"])
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("interview", help="stage 5: interview agents in character")
    p.add_argument("name")
    p.add_argument("--question", "-q", required=True)
    p.add_argument("--agents", action="append", help="comma-separated ids/usernames/names")
    p.add_argument("--type", help="pick agents of this entity type")
    p.add_argument("--sample", type=int, help="how many agents to sample (default 5)")
    p.add_argument("--all", action="store_true", help="interview every matching agent")
    p.add_argument("--followup", action="store_true", help="include earlier interview turns with the same agents")
    llm_flags(p)
    p.set_defaults(func=cmd_interview)

    p = sub.add_parser("fork", help="same agents, fresh world state (A/B test a change)")
    p.add_argument("source")
    p.add_argument("name")
    p.add_argument("--seed", action="append", help="replace seed material with these files")
    p.add_argument("--posts", help='JSON list [{"poster": "<agent>", "content": "..."}] of kick-off posts')
    p.add_argument("--replace-posts", action="store_true", help="drop all previous kick-off posts")
    p.add_argument("--title")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_fork)

    p = sub.add_parser("ingest", help="turn URLs and files into seed.md + subject.json")
    p.add_argument("sources", nargs="+", help="URLs or files: html, pdf, docx, srt/vtt, md/txt, csv, images, videos")
    p.add_argument("--kind", default="generic", help="subject kind (see `kinds`)")
    p.add_argument("--out", required=True, help="output folder (e.g. .cohorte/drafts/<name>)")
    p.add_argument("--title")
    p.add_argument("--notes", help="context: release date, price, channel, audience")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("kinds", help="list subject kinds and their rating dimensions")
    p.set_defaults(func=cmd_kinds)

    p = sub.add_parser("assess", help="panel rating: potential score + confidence index")
    p.add_argument("name")
    p.add_argument("--runs", type=int, default=3, help="repeat the panel in different exposure contexts (default 3)")
    p.add_argument("--models", help="comma-separated models to cross-check, e.g. opus,sonnet")
    p.add_argument("--dry-run", action="store_true", help="only print how many LLM calls it would make")
    llm_flags(p)
    p.set_defaults(func=cmd_assess)

    p = sub.add_parser("calibrate", help="record real outcomes and fit them to predictions")
    p.add_argument("action", choices=["add", "fit", "show"])
    p.add_argument("--sim", help="(add) simulation whose assessment predicted the outcome")
    p.add_argument("--actual", type=float, help="(add) the real outcome value")
    p.add_argument("--scale", type=float, nargs=2, default=[0, 10], metavar=("MIN", "MAX"),
                   help="(add) scale of the real outcome, e.g. 0 10 for a rating, 0 100 for a percentage")
    p.add_argument("--metric", help="(add) what was measured, e.g. 'IMDb rating'")
    p.add_argument("--note")
    p.add_argument("--kind", help="(fit/show) subject kind")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("backtest", help="score Cohorte against a single-prompt baseline on known outcomes")
    p.add_argument("cases", help="folder of case files, a .json or a .jsonl (see benchmarks/README.md)")
    p.add_argument("--out", help="output folder (default <home>/backtests/<cases name>); re-running resumes")
    p.add_argument("--runs", type=int, default=2, help="assessment runs per variant, for both methods (default 2)")
    p.add_argument("--models", help="comma-separated models for the cohort ensemble")
    p.add_argument("--only", help="comma-separated case ids")
    p.add_argument("--max-cases", type=int)
    p.add_argument("--budget", type=float, help="stop before starting a new case once this many USD are spent")
    p.add_argument("--calibrate", action="store_true",
                   help="record real (non-synthetic) outcomes as calibration pairs in --home")
    p.add_argument("--dry-run", action="store_true", help="only print the number of LLM calls")
    llm_flags(p, defaults=True)
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("compare", help="compare metrics of two simulations")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_compare)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except AwaitingResponses as e:
        print(f"[manual] {e.count} LLM request(s) need answers: see {e.path}\n"
              f"Write {{\"<id>\": \"<response>\"}} to responses.json next to it, then re-run the same command.")
        return 3
    except CohorteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted - completed rounds are saved; re-run to resume.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

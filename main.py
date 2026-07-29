from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from src.capture.clean_monitor import capture_real_agent
from src.core.error_gate import AgentDataError
from src.core.llm_assistant import build_llm_assistant_config
from src.core.profile import load_profile
from src.core.runner import run_evaluation
from src.core.webshow_index import build_webshow_index


ROOT = Path(__file__).resolve().parent
DEFAULT_URL = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate captured website Agent logs against a reusable dataset.")
    parser.add_argument("--target", default="", help="Shortcut as <profile>.<mode>.<suite>, for example my_agent.real.regression.")
    parser.add_argument("--profile", default="", help="Domain profile id, for example my_agent.")
    parser.add_argument("--mode", choices=["mock", "events", "real"], default="mock", help="mock=sample log, events=existing jsonl, real=open browser and capture")
    parser.add_argument("--suite", default="regression", help="l1, l2, safety, regression")
    parser.add_argument("--events", default="")
    parser.add_argument("--out-dir", default=str(ROOT / "reports" / "markdown"))
    parser.add_argument("--log-dir", default=str(ROOT / "logs"))
    parser.add_argument("--name", default="", help="Deprecated display hint. Report names now use <sequence>_<yy_mm_dd_hh>.")
    parser.add_argument("--suffix", default="", help="Deprecated alias for --name.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--image-detail-limit", type=int, default=10)
    parser.add_argument("--capture-only", action="store_true", help="In real mode, stop after writing events jsonl and skip evaluation/report generation.")
    parser.add_argument("--llm-assistant", action="store_true", help="Enable optional LLM assistant modules: judge and summary.")
    parser.add_argument("--llm-assistant-model", default="", help="LLM assistant model name. Can also use SITE_AGENT_EVAL_LLM_MODEL.")
    parser.add_argument("--llm-assistant-base-url", default="", help="OpenAI-compatible base URL. Can also use SITE_AGENT_EVAL_LLM_BASE_URL.")
    parser.add_argument("--llm-assistant-api-key-env", default="SITE_AGENT_EVAL_LLM_API_KEY", help="Environment variable containing the LLM assistant API key.")
    parser.add_argument("--llm-assistant-blend", type=float, default=0.80, help="Blend ratio inside judged dimensions, capped at 1.00.")
    args = parser.parse_args()
    _apply_target(args)

    log_dir = Path(args.log_dir)
    out_dir = Path(args.out_dir)
    profile = load_profile(args.profile or None)
    if profile and args.url == DEFAULT_URL and profile.get("default_url"):
        args.url = profile["default_url"]

    run_label = _next_run_label(out_dir)
    run_out_dir = out_dir / run_label

    if args.mode == "mock":
        events_path = Path(args.events) if args.events else ROOT / "sample_logs" / "sample_green_man_events.jsonl"
    elif args.mode == "events":
        if not args.events:
            raise SystemExit("--mode events requires --events")
        events_path = Path(args.events)
    else:
        if not args.url:
            raise SystemExit("--mode real requires --url or a profile with default_url")
        log_dir.mkdir(parents=True, exist_ok=True)
        events_path = log_dir / f"{run_label}_events.jsonl"
        print(f"[REAL CAPTURE] url={args.url}")
        print(f"[REAL CAPTURE] events={events_path}")
        print("[REAL CAPTURE] Finish the browser test, then press Ctrl+C in this terminal to evaluate.")
        capture_real_agent(
            url=args.url,
            events_path=events_path,
            image_detail_limit=args.image_detail_limit,
            capture_config=profile.get("capture") if profile else None,
            normalizer_config=profile.get("normalizer_map") if profile else None,
        )
        if args.capture_only:
            print("Capture complete:")
            print(f"- events: {events_path}")
            print("Evaluation skipped because --capture-only was set.")
            return

    try:
        paths = run_evaluation(
            suite=args.suite,
            events_path=events_path,
            out_dir=run_out_dir,
            run_label=run_label,
            image_detail_limit=args.image_detail_limit,
            profile=profile,
            llm_assistant_config=build_llm_assistant_config(
                enabled=args.llm_assistant,
                model=args.llm_assistant_model,
                base_url=args.llm_assistant_base_url,
                api_key_env=args.llm_assistant_api_key_env,
                blend=args.llm_assistant_blend,
            ),
        )
    except AgentDataError as exc:
        print("[AGENT DATA ERROR] Evaluation aborted before report generation.")
        print(f"[AGENT DATA ERROR] {exc.check.summary()}")
        print("[AGENT DATA ERROR] Webshow index was not updated.")
        raise SystemExit(2) from exc
    print("Evaluation complete:")
    for name, path in paths.items():
        print(f"- {name}: {path}")
    webshow_index = build_webshow_index(ROOT)
    print(f"- webshow_index: {webshow_index}")


def _clean_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return cleaned.strip("_") or "run"


def _apply_target(args: argparse.Namespace) -> None:
    if not args.target:
        return
    parts = args.target.split(".")
    if len(parts) != 3:
        raise SystemExit("--target must be formatted as <profile>.<mode>.<suite>, for example my_agent.real.regression")
    profile, mode, suite = parts
    if mode not in {"mock", "events", "real"}:
        raise SystemExit("--target mode must be one of mock, events, real")
    args.profile = profile
    args.mode = mode
    args.suite = suite


def _next_run_label(out_dir: Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(path for path in out_dir.iterdir() if path.is_dir())
    numbers = []
    for path in existing:
        number_text = path.name.split("_", 1)[0]
        if number_text.isdigit() and len(number_text) == 3:
            numbers.append(int(number_text))
    sequence = (max(numbers) + 1) if numbers else 1
    return f"{sequence:03d}_{datetime.now().strftime('%y_%m_%d_%H')}"


if __name__ == "__main__":
    main()

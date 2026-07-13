from __future__ import annotations

import argparse
from pathlib import Path

from src.capture.clean_monitor import capture_real_agent
from src.core.profile import load_profile
from src.core.runner import run_evaluation


ROOT = Path(__file__).resolve().parent
DEFAULT_URL = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate captured website Agent logs against a reusable dataset.")
    parser.add_argument("--target", default="", help="Shortcut as <profile>.<mode>.<suite>, for example my_agent.real.regression.")
    parser.add_argument("--profile", default="", help="Domain profile id, for example my_agent.")
    parser.add_argument("--mode", choices=["mock", "events", "real"], default="mock", help="mock=sample log, events=existing jsonl, real=open browser and capture")
    parser.add_argument("--suite", default="regression", help="l1, l2, safety, regression")
    parser.add_argument("--events", default="")
    parser.add_argument("--out-dir", default=str(ROOT / "reports"))
    parser.add_argument("--log-dir", default=str(ROOT / "logs"))
    parser.add_argument("--name", default="", help="Evaluation run name. Files are written as <name>_001_*.jsonl/md.")
    parser.add_argument("--suffix", default="", help="Deprecated alias for --name.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--image-detail-limit", type=int, default=10)
    args = parser.parse_args()
    _apply_target(args)

    log_dir = Path(args.log_dir)
    out_dir = Path(args.out_dir)
    profile = load_profile(args.profile or None)
    if profile and args.url == DEFAULT_URL and profile.get("default_url"):
        args.url = profile["default_url"]

    run_name = _clean_name(args.name or args.suffix or "_".join(part for part in [args.profile, args.mode, args.suite] if part))
    run_out_dir = out_dir / run_name
    run_label = _next_run_label(run_out_dir, run_name)

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

    paths = run_evaluation(
        suite=args.suite,
        events_path=events_path,
        out_dir=run_out_dir,
        run_label=run_label,
        image_detail_limit=args.image_detail_limit,
        profile=profile,
    )
    print("Evaluation complete:")
    for name, path in paths.items():
        print(f"- {name}: {path}")


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


def _next_run_label(run_out_dir: Path, run_name: str) -> str:
    existing = sorted(run_out_dir.glob(f"{run_name}_*_eval_report.md"))
    numbers = []
    for path in existing:
        stem = path.stem
        prefix = f"{run_name}_"
        suffix = "_eval_report"
        if not stem.startswith(prefix) or not stem.endswith(suffix):
            continue
        number_text = stem[len(prefix) : -len(suffix)]
        if number_text.isdigit():
            numbers.append(int(number_text))
    return f"{run_name}_{(max(numbers) + 1) if numbers else 1:03d}"


if __name__ == "__main__":
    main()

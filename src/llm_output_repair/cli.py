"""Local-only CLI for replaying dumps and promoting explicit regression tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .regression import (
    _decode_json, case_digest, fixture_paths, load_case,
    matches_expected, promote_dump, replay_case,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-repair",
        description="Replay captured LLM parse failures; no inference or uploads.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    replay = commands.add_parser("replay", help="Replay a dump or regression fixture")
    replay.add_argument("path", type=Path)

    promote = commands.add_parser(
        "promote", help="Create a data-only regression fixture from a crash dump"
    )
    promote.add_argument("dump", type=Path)
    promote.add_argument("--corpus", type=Path, required=True)
    promote.add_argument("--bucket", choices=["known_bad", "fixed"], default="known_bad")
    choice = promote.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--expect-json-file", type=Path,
        help="File containing the exact expected JSON value (not shell history)",
    )
    choice.add_argument(
        "--expect-error", metavar="EXCEPTION",
        help="Expected exception class name, e.g. ValueError",
    )

    verify = commands.add_parser("verify", help="Run regression fixtures in a corpus")
    verify.add_argument("--corpus", type=Path, required=True)
    verify.add_argument(
        "--bucket", choices=["fixed", "known_bad", "all"], default="fixed"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "replay":
            case = load_case(args.path)
            observation = replay_case(case)
            output = {
                "case_id": case_digest(case),
                "operation": case["operation"],
                **observation.safe_summary(),
            }
            if "expected" in case:
                output["matches_expected"] = matches_expected(case, observation)
                exit_status = 0 if output["matches_expected"] else 1
            else:
                exit_status = 0 if observation.status == "accepted" else 1
            print(json.dumps(output, sort_keys=True))
            return exit_status

        if args.command == "promote":
            if args.expect_json_file is not None:
                value_path = args.expect_json_file
                if value_path.is_symlink() or value_path.stat().st_size > 16_000_000:
                    raise ValueError("Expected value file must be a bounded regular file")
                expected = {
                    "status": "accepted",
                    "value": _decode_json(value_path.read_text(encoding="utf-8")),
                }
            else:
                expected = {"status": "rejected", "exception_type": args.expect_error}
            path = promote_dump(
                args.dump, corpus=args.corpus, expected=expected, bucket=args.bucket
            )
            print(json.dumps({
                "status": "promoted", "bucket": args.bucket,
                "case_id": load_case(path)["case_id"], "path": str(path)
            }, sort_keys=True))
            return 0

        failures = 0
        total = 0
        for path in fixture_paths(args.corpus, bucket=args.bucket):
            total += 1
            try:
                case = load_case(path)
                matched = matches_expected(case, replay_case(case))
                case_id = case_digest(case)
            except Exception:
                matched = False
                case_id = path.stem  # Never print file content or exception messages.
            if not matched:
                failures += 1
            print(json.dumps({
                "case_id": case_id, "matches_expected": matched,
                "fixture": path.name,
            }, sort_keys=True))
        print(json.dumps({"total": total, "passed": total - failures, "failed": failures}))
        # Empty corpus must not silently pass as a verification result.
        return 0 if total and not failures else 1
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        # Exception messages, including JSON decoder snippets and raw filenames,
        # are omitted to avoid leaking model output into logs.
        print(f"llm-repair: {type(exc).__name__}: operation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

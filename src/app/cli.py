from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Force UTF-8 output on Windows (avoids UnicodeEncodeError with Vietnamese text)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.graph import ShoppingAssistant


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VinShop Multi-Agent Shopping Assistant")
    parser.add_argument("--question", help="Run one question through the graph.")
    parser.add_argument("--test-file", default="data/test.json")
    parser.add_argument("--trace-file", default=None)
    parser.add_argument("--output-dir", default="src/artifacts/traces")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    assistant = ShoppingAssistant()

    if args.batch:
        summary = assistant.run_batch(
            test_file=Path(args.test_file),
            output_dir=Path(args.output_dir),
            rebuild_index=args.rebuild_index,
        )
        print(f"\n=== Batch Results ===")
        print(f"Total: {summary['total']} | Passed: {summary['passed']} | Failed: {summary['failed']}")
        print(f"Summary saved to: {Path(args.output_dir) / 'summary.json'}")
        for r in summary["results"]:
            status_icon = "✓" if r.get("passed") else "✗"
            print(f"  [{status_icon}] {r['id']}: {r['question'][:60]}")

    elif args.question:
        trace_file = Path(args.trace_file) if args.trace_file else None
        result = assistant.ask(
            question=args.question,
            trace_file=trace_file,
            rebuild_index=args.rebuild_index,
        )
        print(f"\n=== Answer ===")
        print(result.get("final_answer", ""))
        if trace_file:
            print(f"\nTrace saved to: {trace_file}")

    else:
        build_parser().print_help()


if __name__ == "__main__":
    main()

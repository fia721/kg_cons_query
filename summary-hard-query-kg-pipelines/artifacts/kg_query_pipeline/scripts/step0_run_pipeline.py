#!/usr/bin/env python3
"""Step 0: run Step 1-3 end to end.

Default behavior for this task:
  - synthesize 10 query/rubric records
  - build a 30-row evaluation set by exporting 10 queries x 3 samples

Inputs:
  --train-jsonl training data with messages/tool context.

Outputs under --output-dir:
  step1.materials.jsonl
  step2.query_rubrics.raw.jsonl
  step2.query_rubrics.jsonl
  step2.query_rubrics.jsonl.semantic_rejected
  step3.eval_dataset.csv
  step3.eval_dataset.xlsx
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from pipeline_utils import DEFAULT_TRAIN_JSONL, PIPELINE_DIR


def run(cmd: list[str], env: dict[str, str]) -> None:
    print("[run]", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-jsonl", type=Path, default=DEFAULT_TRAIN_JSONL)
    parser.add_argument("--output-dir", type=Path, default=PIPELINE_DIR / "outputs" / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--provider", choices=["ark", "modelhub"], default="ark")
    parser.add_argument("--model", default="ep-20260225140859-njzr9")
    parser.add_argument("--start-line", type=int, default=1)
    parser.add_argument("--synth-limit", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int, default=30, help="How many training rows to extract as candidates before filtering.")
    parser.add_argument("--eval-query-limit", type=int, default=10)
    parser.add_argument("--samples-per-query", type=int, default=3)
    parser.add_argument("--similarity-threshold", type=float, default=0.85)
    parser.add_argument("--semantic-similarity-threshold", type=float, default=0.85)
    parser.add_argument("--semantic-candidate-multiplier", type=int, default=2)
    parser.add_argument("--max-docs", type=int, default=4)
    parser.add_argument("--max-chars-per-doc", type=int, default=900)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scripts = Path(__file__).resolve().parent
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()

    materials = args.output_dir / "step1.materials.jsonl"
    raw_rubrics = args.output_dir / "step2.query_rubrics.raw.jsonl"
    rubrics = args.output_dir / "step2.query_rubrics.jsonl"
    semantic_rejected = args.output_dir / "step2.query_rubrics.jsonl.semantic_rejected"
    eval_csv = args.output_dir / "step3.eval_dataset.csv"
    eval_xlsx = args.output_dir / "step3.eval_dataset.xlsx"
    raw_target_count = max(args.synth_limit, args.synth_limit * max(1, args.semantic_candidate_multiplier))

    run(
        [
            sys.executable,
            str(scripts / "step1_extract_train_materials.py"),
            "--input",
            str(args.train_jsonl),
            "--output",
            str(materials),
            "--start-line",
            str(args.start_line),
            "--limit",
            str(args.candidate_limit),
        ],
        env,
    )
    run(
        [
            sys.executable,
            str(scripts / "step2_synthesize_query_rubric.py"),
            "--input",
            str(materials),
            "--output",
            str(raw_rubrics),
            "--provider",
            args.provider,
            "--model",
            args.model,
            "--limit",
            str(args.candidate_limit),
            "--target-count",
            str(raw_target_count),
            "--similarity-threshold",
            str(args.similarity_threshold),
            "--max-docs",
            str(args.max_docs),
            "--max-chars-per-doc",
            str(args.max_chars_per_doc),
        ],
        env,
    )
    run(
        [
            sys.executable,
            str(scripts / "step2b_filter_semantic_similarity.py"),
            "--input",
            str(raw_rubrics),
            "--output",
            str(rubrics),
            "--rejected-output",
            str(semantic_rejected),
            "--provider",
            args.provider,
            "--model",
            args.model,
            "--threshold",
            str(args.semantic_similarity_threshold),
            "--target-count",
            str(args.synth_limit),
        ],
        env,
    )
    run(
        [
            sys.executable,
            str(scripts / "step3_build_eval_dataset.py"),
            "--input",
            str(rubrics),
            "--output-csv",
            str(eval_csv),
            "--output-xlsx",
            str(eval_xlsx),
            "--max-queries",
            str(args.eval_query_limit),
            "--samples-per-query",
            str(args.samples_per_query),
        ],
        env,
    )
    print(f"[done] output_dir={args.output_dir}")


if __name__ == "__main__":
    main()

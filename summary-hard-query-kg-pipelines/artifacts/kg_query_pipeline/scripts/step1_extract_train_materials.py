#!/usr/bin/env python3
"""Step 1: extract retrieval materials from training JSONL.

Input:
  --input JSONL. Each row is a training sample with messages/tool context.

Output JSONL fields:
  case_id: stable id, default train_line{line_no}
  source_file: input path
  source_line_no: original line number
  original_user_query: first user query in the training sample
  material_count: number of extracted retrieval material blocks
  context_materials: list of {material_id, source, source_query, title, text}

The step only reads retrieval/tool materials. User query is stored for traceability
but is not used as KG/query synthesis input.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline_utils import DEFAULT_TRAIN_JSONL, iter_jsonl, original_user_query, write_jsonl
from extract_retrieval_kg import extract_retrieval_materials, infer_case_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_TRAIN_JSONL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-line", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-materials", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    def rows():
        processed = 0
        for line_no, record in iter_jsonl(args.input):
            if line_no < args.start_line:
                continue
            materials = extract_retrieval_materials(record)
            if len(materials) < args.min_materials:
                continue
            case_id = infer_case_id(record, f"train_line{line_no}")
            yield {
                "case_id": case_id,
                "source_file": str(args.input),
                "source_line_no": line_no,
                "original_user_query": original_user_query(record),
                "material_count": len(materials),
                "context_materials": materials,
            }
            processed += 1
            print(f"[step1] case={case_id} line={line_no} materials={len(materials)}")
            if args.limit and processed >= args.limit:
                break

    count = write_jsonl(args.output, rows())
    print(f"[step1] wrote={args.output} rows={count}")


if __name__ == "__main__":
    main()


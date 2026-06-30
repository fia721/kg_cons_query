#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python "$ROOT_DIR/scripts/step1_build_code_table_kg.py" \
  --sources "$ROOT_DIR/data/open_source_manifest.jsonl" \
  --types "$ROOT_DIR/data/entity_types.jsonl" \
  --properties "$ROOT_DIR/data/property_axes.jsonl" \
  --kg-output "$ROOT_DIR/outputs/permanent_code_table_kg.json" \
  --html-output "$ROOT_DIR/outputs/permanent_code_table_kg.html"

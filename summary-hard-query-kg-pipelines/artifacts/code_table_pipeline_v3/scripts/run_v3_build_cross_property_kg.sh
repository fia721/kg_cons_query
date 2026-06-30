#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python "$ROOT_DIR/scripts/step0_download_open_sources.py" \
  --output-dir "$ROOT_DIR/open_sources"

python "$ROOT_DIR/scripts/step1_build_cross_property_kg.py" \
  --sources "$ROOT_DIR/data/open_source_manifest.jsonl" \
  --types "$ROOT_DIR/data/entity_types.jsonl" \
  --properties "$ROOT_DIR/data/property_axes.jsonl" \
  --entities "$ROOT_DIR/data/entities_seed.jsonl" \
  --kg-output "$ROOT_DIR/outputs/entity_cross_property_kg.json" \
  --candidate-output "$ROOT_DIR/outputs/entity_cross_property_candidates.jsonl" \
  --html-output "$ROOT_DIR/outputs/entity_cross_property_kg.html"

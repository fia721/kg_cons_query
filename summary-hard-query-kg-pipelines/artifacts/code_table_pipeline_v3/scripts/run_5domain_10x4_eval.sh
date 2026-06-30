#!/usr/bin/env bash
# Build a 10x4 evaluation set from five manually selected training domains.
#
# Input:
#   ARK_API_KEY: required for step3 LLM synthesis.
#   TRAIN_FILE: optional training JSONL. Defaults to data/training_data/rl_training_data_v2_mix_grpo_clean_with_context.jsonl.
#   OUTPUT_DIR: optional output directory.
#   MODEL: optional Ark endpoint. Defaults to ep-20260225140859-njzr9.
#   OVERLAY_JSONL: optional open-KB domain overlay JSONL. Defaults to the latest online-KG smoke run overlay.
#   RESULT_CSV: optional rollout/eval result CSV. If set, report includes answers and good query cases.
#
# Output:
#   step3.<domain>.boundary_queries.jsonl/md/rejected.jsonl
#   step3.boundary_queries.10.jsonl
#   step4.eval_dataset_10q_x4.csv
#   step4.eval_dataset_10q_x4.xlsx
#
# Notes:
#   The step3 prompt only receives retrieval materials plus code-table/open-KB schemas.
#   original_query is retained only as a trace/filter field after synthesis.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/../.." && pwd)"

if [[ -z "${ARK_API_KEY:-}" ]]; then
  echo "ERROR: ARK_API_KEY is required" >&2
  exit 2
fi

TRAIN_FILE="${TRAIN_FILE:-$REPO_DIR/data/training_data/rl_training_data_v2_mix_grpo_clean_with_context.jsonl}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/outputs/run_20260630_v3_5domains_10x4}"
MODEL="${MODEL:-ep-20260225140859-njzr9}"
OVERLAY_JSONL="${OVERLAY_JSONL:-$ROOT_DIR/outputs/run_20260630_v3_onlinekg_walk_10case/step2.open_kb_domain_overlays.jsonl}"
MAX_MATERIAL_CHARS="${MAX_MATERIAL_CHARS:-9000}"
SIMILARITY_THRESHOLD="${SIMILARITY_THRESHOLD:-0.92}"

mkdir -p "$OUTPUT_DIR"

declare -A DOMAIN_LINES
DOMAIN_LINES[finance_payment]="45,82,86,104,424,425,540,594,725,752,769,851,878,995,1078,1092,1162,1180,1251,1592"
DOMAIN_LINES[ecommerce_local_life]="1,12,39,52,71,81,94,115,131,195,262,290,312,371,491,763,775,912,916,991"
DOMAIN_LINES[software_collaboration]="4,6,7,10,16,33,46,51,80,100,116,166,191,211,234,395,416,452,1460,1633"
DOMAIN_LINES[hr_org_management]="2,5,8,67,95,130,185,189,250,284,436,589,776,978,1098,1104,1124,1404,1419,1444"
DOMAIN_LINES[legal_compliance]="11,31,112,124,148,149,151,255,525,550,553,809,1029,1131,1171,1233,1328,1543,1603,1680"

DOMAINS=(
  finance_payment
  ecommerce_local_life
  software_collaboration
  hr_org_management
  legal_compliance
)

selection_jsonl="$OUTPUT_DIR/domain_line_selection.jsonl"
: > "$selection_jsonl"
for domain in "${DOMAINS[@]}"; do
  printf '{"domain_id":"%s","line_nos":"%s"}\n' "$domain" "${DOMAIN_LINES[$domain]}" >> "$selection_jsonl"
done

for domain in "${DOMAINS[@]}"; do
  existing_jsonl="$OUTPUT_DIR/step3.$domain.boundary_queries.jsonl"
  if [[ -s "$existing_jsonl" ]]; then
    existing_count="$(wc -l < "$existing_jsonl" | tr -d ' ')"
    if [[ "$existing_count" -ge 2 ]]; then
      echo "[5domain] skip domain=$domain existing_count=$existing_count"
      continue
    fi
  fi
  echo "[5domain] synthesize domain=$domain line_nos=${DOMAIN_LINES[$domain]}"
  python "$ROOT_DIR/scripts/step3_synthesize_boundary_queries_from_train.py" \
    --train-file "$TRAIN_FILE" \
    --types "$ROOT_DIR/data/entity_types.jsonl" \
    --properties "$ROOT_DIR/data/property_axes.jsonl" \
    --domain-overlays "$OVERLAY_JSONL" \
    --output-jsonl "$OUTPUT_DIR/step3.$domain.boundary_queries.jsonl" \
    --output-md "$OUTPUT_DIR/step3.$domain.boundary_queries.md" \
    --rejected-jsonl "$OUTPUT_DIR/step3.$domain.rejected_queries.jsonl" \
    --model "$MODEL" \
    --max-material-chars "$MAX_MATERIAL_CHARS" \
    --target-count 2 \
    --candidate-limit 20 \
    --similarity-threshold "$SIMILARITY_THRESHOLD" \
    --line-nos "${DOMAIN_LINES[$domain]}"
  count="$(wc -l < "$OUTPUT_DIR/step3.$domain.boundary_queries.jsonl" | tr -d ' ')"
  if [[ "$count" -lt 2 ]]; then
    echo "ERROR: domain=$domain accepted only $count rows, expected 2" >&2
    exit 3
  fi
done

merged_jsonl="$OUTPUT_DIR/step3.boundary_queries.10.jsonl"
: > "$merged_jsonl"
for domain in "${DOMAINS[@]}"; do
  cat "$OUTPUT_DIR/step3.$domain.boundary_queries.jsonl" >> "$merged_jsonl"
done

python "$ROOT_DIR/scripts/step4_build_eval_dataset_from_v3_queries.py" \
  --input "$merged_jsonl" \
  --output-csv "$OUTPUT_DIR/step4.eval_dataset_10q_x4.csv" \
  --output-xlsx "$OUTPUT_DIR/step4.eval_dataset_10q_x4.xlsx" \
  --samples-per-query 4

report_args=()
if [[ -n "${RESULT_CSV:-}" ]]; then
  report_args+=(--result-csv "$RESULT_CSV")
  report_args+=(--good-case-html "$OUTPUT_DIR/step5.good_cases_10q_x4.html")
fi

python "$ROOT_DIR/scripts/step5_build_v3_report_html.py" \
  --queries-jsonl "$merged_jsonl" \
  --eval-csv "$OUTPUT_DIR/step4.eval_dataset_10q_x4.csv" \
  --output-html "$OUTPUT_DIR/step5.report_10q_x4.html" \
  "${report_args[@]}"

echo "[5domain] done"
echo "[5domain] queries=$merged_jsonl"
echo "[5domain] eval_xlsx=$OUTPUT_DIR/step4.eval_dataset_10q_x4.xlsx"
echo "[5domain] report_html=$OUTPUT_DIR/step5.report_10q_x4.html"
if [[ -n "${RESULT_CSV:-}" ]]; then
  echo "[5domain] good_case_html=$OUTPUT_DIR/step5.good_cases_10q_x4.html"
fi

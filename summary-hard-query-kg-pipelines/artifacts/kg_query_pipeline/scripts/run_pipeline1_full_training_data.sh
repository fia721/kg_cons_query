#!/usr/bin/env bash
# Run pipeline1 end-to-end on all JSONL files in data/training_data.
#
# Inputs:
#   TRAIN_DIR: training JSONL directory, default data/training_data
#   OUTPUT_ROOT: output directory root, default artifacts/kg_query_pipeline/outputs
#   ARK_API_KEY: required Ark API key
#
# Optional config:
#   MODEL: Ark endpoint model, default ep-20260225140859-njzr9
#   PROVIDER: ark/modelhub, default ark
#   SAMPLES_PER_QUERY: repeated eval rows per accepted query, default 3
#   MAX_DOCS: max retrieved docs passed to synthesis LLM, default 4
#   MAX_CHARS_PER_DOC: max chars per doc passed to synthesis LLM, default 900
#   SIMILARITY_THRESHOLD: string similarity filter threshold, default 0.85
#   SEMANTIC_SIMILARITY_THRESHOLD: LLM semantic similarity filter threshold, default 0.85
#   CONCURRENCY: concurrent LLM requests for step2/step2b, default 10
#   TARGET_COUNT: accepted query target for step2/step2b. 0 means no cap, default 0
#   MAX_QUERIES: max queries exported by step3. 0 means all, default 0
#   RUN_ID: fixed run id for resume. If omitted, a new timestamp run is created.
#   RESUME: 1 skips steps with .done markers or complete-looking outputs, default 1
#   FORCE: 1 reruns every step, default 0
#   ALLOW_OVERWRITE_PARTIAL: 1 allows rerunning a step whose outputs exist
#     but whose .done marker is missing. Existing partial outputs are backed up
#     first. Default 0, which fails fast to avoid accidental overwrite.
#   RESUME_PARTIAL_JSONL: 1 lets step2/step2b append and skip existing case_id
#     rows when outputs exist but .done marker is missing, default 1
#   QUERY_SURFACE_REWRITE: 1 rewrites query wording after step2b while keeping
#     rubrics unchanged, default 0
#
# Outputs per training file:
#   step1.materials.jsonl
#   step2.query_rubrics.raw.jsonl
#   step2.query_rubrics.raw.jsonl.rejected
#   step2.query_rubrics.jsonl
#   step2.query_rubrics.jsonl.semantic_rejected
#   step2.query_rubrics.rewritten.jsonl, when QUERY_SURFACE_REWRITE=1
#   step3.eval_dataset.csv
#   step3.eval_dataset.xlsx
#   pipeline.log

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

TRAIN_DIR="${TRAIN_DIR:-data/training_data}"
OUTPUT_ROOT="${OUTPUT_ROOT:-artifacts/kg_query_pipeline/outputs}"
PROVIDER="${PROVIDER:-ark}"
MODEL="${MODEL:-ep-20260225140859-njzr9}"
SAMPLES_PER_QUERY="${SAMPLES_PER_QUERY:-3}"
MAX_DOCS="${MAX_DOCS:-4}"
MAX_CHARS_PER_DOC="${MAX_CHARS_PER_DOC:-900}"
SIMILARITY_THRESHOLD="${SIMILARITY_THRESHOLD:-0.85}"
SEMANTIC_SIMILARITY_THRESHOLD="${SEMANTIC_SIMILARITY_THRESHOLD:-0.85}"
CONCURRENCY="${CONCURRENCY:-10}"
TARGET_COUNT="${TARGET_COUNT:-0}"
MAX_QUERIES="${MAX_QUERIES:-0}"
RESUME="${RESUME:-1}"
FORCE="${FORCE:-0}"
ALLOW_OVERWRITE_PARTIAL="${ALLOW_OVERWRITE_PARTIAL:-0}"
RESUME_PARTIAL_JSONL="${RESUME_PARTIAL_JSONL:-1}"
QUERY_SURFACE_REWRITE="${QUERY_SURFACE_REWRITE:-0}"

if [[ "$PROVIDER" == "ark" && -z "${ARK_API_KEY:-}" ]]; then
  echo "ERROR: ARK_API_KEY is required when PROVIDER=ark" >&2
  exit 1
fi

if [[ ! -d "$TRAIN_DIR" ]]; then
  echo "ERROR: TRAIN_DIR does not exist: $TRAIN_DIR" >&2
  exit 1
fi

mapfile -t TRAIN_FILES < <(find "$TRAIN_DIR" -maxdepth 1 -type f -name '*.jsonl' | sort)
if [[ "${#TRAIN_FILES[@]}" -eq 0 ]]; then
  echo "ERROR: no JSONL files found under $TRAIN_DIR" >&2
  exit 1
fi

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
echo "[pipeline1-full] run_id=$RUN_ID files=${#TRAIN_FILES[@]} provider=$PROVIDER model=$MODEL concurrency=$CONCURRENCY"

should_skip_step() {
  local done_marker="$1"
  local _output_file="$2"
  if [[ "$FORCE" == "1" ]]; then
    return 1
  fi
  if [[ "$RESUME" == "1" && -f "$done_marker" ]]; then
    return 0
  fi
  return 1
}

has_any_output() {
  local output_file
  for output_file in "$@"; do
    if [[ -e "$output_file" ]]; then
      return 0
    fi
  done
  return 1
}

backup_outputs() {
  local backup_suffix="$1"
  shift
  local output_file
  for output_file in "$@"; do
    if [[ -e "$output_file" ]]; then
      mv "$output_file" "${output_file}.${backup_suffix}.bak"
      echo "[pipeline1-full] backed up partial output: $output_file -> ${output_file}.${backup_suffix}.bak"
    fi
  done
}

guard_partial_step() {
  local step_name="$1"
  local done_marker="$2"
  shift 2

  if [[ -f "$done_marker" || "$FORCE" == "1" ]]; then
    return 0
  fi
  if ! has_any_output "$@"; then
    return 0
  fi

  if [[ "$ALLOW_OVERWRITE_PARTIAL" == "1" ]]; then
    backup_outputs "partial_$(date +%Y%m%d_%H%M%S)" "$@"
    return 0
  fi

  echo "ERROR: $step_name has existing output but missing done marker: $done_marker" >&2
  echo "ERROR: refusing to overwrite partial outputs. Use a new RUN_ID, or set ALLOW_OVERWRITE_PARTIAL=1 to back up and rerun this step." >&2
  for output_file in "$@"; do
    if [[ -e "$output_file" ]]; then
      echo "ERROR: partial output exists: $output_file" >&2
    fi
  done
  exit 2
}

for train_file in "${TRAIN_FILES[@]}"; do
  name="$(basename "$train_file" .jsonl)"
  output_dir="$OUTPUT_ROOT/full_${name}_${RUN_ID}"
  mkdir -p "$output_dir"
  log_file="$output_dir/pipeline.log"
  line_count="$(wc -l < "$train_file" | tr -d ' ')"

  {
    echo "[pipeline1-full] start file=$train_file lines=$line_count output_dir=$output_dir"
    date '+[pipeline1-full] started_at=%Y-%m-%d %H:%M:%S'

    if [[ "$RESUME" == "1" && ! -f "$output_dir/step1.done" && -s "$output_dir/step1.materials.jsonl" ]]; then
      existing_step1_lines="$(wc -l < "$output_dir/step1.materials.jsonl" | tr -d ' ')"
      if [[ "$existing_step1_lines" == "$line_count" ]]; then
        echo "[pipeline1-full] mark existing step1 done lines=$existing_step1_lines"
        touch "$output_dir/step1.done"
      fi
    fi
    if [[ "$RESUME" == "1" && ! -f "$output_dir/step2.done" && -s "$output_dir/step2.query_rubrics.jsonl" ]]; then
      echo "[pipeline1-full] mark existing step2 done because filtered output exists"
      touch "$output_dir/step2.done"
    fi
    if [[ "$RESUME" == "1" && ! -f "$output_dir/step2b.done" && -s "$output_dir/step3.eval_dataset.xlsx" ]]; then
      echo "[pipeline1-full] mark existing step2b done because eval dataset exists"
      touch "$output_dir/step2b.done"
    fi
    if [[ "$RESUME" == "1" && ! -f "$output_dir/step2c.done" && -s "$output_dir/step2.query_rubrics.rewritten.jsonl" ]]; then
      echo "[pipeline1-full] mark existing step2c done because rewritten output exists"
      touch "$output_dir/step2c.done"
    fi
    if [[ "$RESUME" == "1" && ! -f "$output_dir/step3.done" && -s "$output_dir/step3.eval_dataset.xlsx" ]]; then
      echo "[pipeline1-full] mark existing step3 done because xlsx exists"
      touch "$output_dir/step3.done"
    fi

    if should_skip_step "$output_dir/step1.done" "$output_dir/step1.materials.jsonl"; then
      echo "[pipeline1-full] skip step1 existing=$output_dir/step1.materials.jsonl"
    else
      guard_partial_step "step1" "$output_dir/step1.done" \
        "$output_dir/step1.materials.jsonl"
      python artifacts/kg_query_pipeline/scripts/step1_extract_train_materials.py \
        --input "$train_file" \
        --output "$output_dir/step1.materials.jsonl" \
        --start-line 1 \
        --limit "$line_count"
      touch "$output_dir/step1.done"
    fi

    if should_skip_step "$output_dir/step2.done" "$output_dir/step2.query_rubrics.raw.jsonl"; then
      echo "[pipeline1-full] skip step2 existing=$output_dir/step2.query_rubrics.raw.jsonl"
    else
      step2_resume_args=()
      if [[ "$RESUME" == "1" && "$FORCE" != "1" && "$RESUME_PARTIAL_JSONL" == "1" ]]; then
        step2_resume_args=(--resume)
      else
        guard_partial_step "step2" "$output_dir/step2.done" \
          "$output_dir/step2.query_rubrics.raw.jsonl" \
          "$output_dir/step2.query_rubrics.raw.jsonl.rejected"
      fi
      python -u artifacts/kg_query_pipeline/scripts/step2_synthesize_query_rubric.py \
        --input "$output_dir/step1.materials.jsonl" \
        --output "$output_dir/step2.query_rubrics.raw.jsonl" \
        --provider "$PROVIDER" \
        --model "$MODEL" \
        --limit "$line_count" \
        --target-count "$TARGET_COUNT" \
        --similarity-threshold "$SIMILARITY_THRESHOLD" \
        --max-docs "$MAX_DOCS" \
        --max-chars-per-doc "$MAX_CHARS_PER_DOC" \
        --concurrency "$CONCURRENCY" \
        "${step2_resume_args[@]}"
      touch "$output_dir/step2.done"
    fi

    if should_skip_step "$output_dir/step2b.done" "$output_dir/step2.query_rubrics.jsonl"; then
      echo "[pipeline1-full] skip step2b existing=$output_dir/step2.query_rubrics.jsonl"
    else
      step2b_resume_args=()
      if [[ "$RESUME" == "1" && "$FORCE" != "1" && "$RESUME_PARTIAL_JSONL" == "1" ]]; then
        step2b_resume_args=(--resume)
      else
        guard_partial_step "step2b" "$output_dir/step2b.done" \
          "$output_dir/step2.query_rubrics.jsonl" \
          "$output_dir/step2.query_rubrics.jsonl.semantic_rejected"
      fi
      python -u artifacts/kg_query_pipeline/scripts/step2b_filter_semantic_similarity.py \
        --input "$output_dir/step2.query_rubrics.raw.jsonl" \
        --output "$output_dir/step2.query_rubrics.jsonl" \
        --rejected-output "$output_dir/step2.query_rubrics.jsonl.semantic_rejected" \
        --provider "$PROVIDER" \
        --model "$MODEL" \
        --threshold "$SEMANTIC_SIMILARITY_THRESHOLD" \
        --target-count "$TARGET_COUNT" \
        --concurrency "$CONCURRENCY" \
        "${step2b_resume_args[@]}"
      touch "$output_dir/step2b.done"
    fi

    step3_input="$output_dir/step2.query_rubrics.jsonl"
    if [[ "$QUERY_SURFACE_REWRITE" == "1" ]]; then
      if should_skip_step "$output_dir/step2c.done" "$output_dir/step2.query_rubrics.rewritten.jsonl"; then
        echo "[pipeline1-full] skip step2c existing=$output_dir/step2.query_rubrics.rewritten.jsonl"
      else
        guard_partial_step "step2c" "$output_dir/step2c.done" \
          "$output_dir/step2.query_rubrics.rewritten.jsonl"
        python artifacts/kg_query_pipeline/scripts/step2c_rewrite_query_surface.py \
          --input "$output_dir/step2.query_rubrics.jsonl" \
          --output "$output_dir/step2.query_rubrics.rewritten.jsonl"
        touch "$output_dir/step2c.done"
      fi
      step3_input="$output_dir/step2.query_rubrics.rewritten.jsonl"
    fi

    if should_skip_step "$output_dir/step3.done" "$output_dir/step3.eval_dataset.xlsx"; then
      echo "[pipeline1-full] skip step3 existing=$output_dir/step3.eval_dataset.xlsx"
    else
      guard_partial_step "step3" "$output_dir/step3.done" \
        "$output_dir/step3.eval_dataset.csv" \
        "$output_dir/step3.eval_dataset.xlsx"
      python artifacts/kg_query_pipeline/scripts/step3_build_eval_dataset.py \
        --input "$step3_input" \
        --output-csv "$output_dir/step3.eval_dataset.csv" \
        --output-xlsx "$output_dir/step3.eval_dataset.xlsx" \
        --max-queries "$MAX_QUERIES" \
        --samples-per-query "$SAMPLES_PER_QUERY"
      touch "$output_dir/step3.done"
    fi

    date '+[pipeline1-full] finished_at=%Y-%m-%d %H:%M:%S'
    echo "[pipeline1-full] done output_dir=$output_dir"
  } 2>&1 | tee -a "$log_file"
done

echo "[pipeline1-full] all done run_id=$RUN_ID"

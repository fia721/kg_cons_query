#!/usr/bin/env bash
# Pipeline2 V3 end-to-end runner.
#
# 功能：
#   1. 构建基础永久码表 KG。
#   2. 构建 open-KB-backed 专用领域 overlay KG。
#   3. 从训练数据召回材料合成边界 query/rubric，并做结构化过滤。
#   4. 构造测评集 CSV/XLSX。
#   5. 生成 HTML 报告。
#
# 主要环境变量：
#   ARK_API_KEY: step3 调用 LLM 必需。
#   RUN_ID: 输出 run id，默认当前时间。
#   TRAIN_FILE: 训练数据 JSONL。
#   TARGET_COUNT: 接受的 query 数，默认 10。
#   CANDIDATE_LIMIT: 扫描候选行数，默认 60。
#   SAMPLES_PER_QUERY: 每条 query 采样次数，默认 4。
#   LINE_NOS: 可选，逗号分隔行号；设置后只跑这些行。
#   DOWNLOAD_SOURCE_TERMS: 设为 1 时下载 Schema.org/DBpedia/NAICS/NIST 原始文件；默认不下载，只使用 Wikidata 在线缓存和已有本地缓存。
#   ONLINE_KG_PREFLIGHT: 设为 1 时先验证 Wikidata 在线 KG；不可用则自动禁用在线 Wikidata，默认 1。
#   WIKIDATA_TIMEOUT: 单次 Wikidata 请求超时秒数，默认 8。
#   IGNORE_PROXY_FOR_WIKIDATA: 设为 1 时访问 Wikidata 绕过 http_proxy/https_proxy，默认 1。
#   WIKIDATA_SEARCH_LIMIT: 每个 seed term 从 Wikidata search 取几个命中，默认 3。
#   WIKIDATA_MAX_NEIGHBORS_PER_PROP: 每个 Wikidata 属性最多保留几个一跳邻居，默认 3。
#   RESULT_CSV: 可选，测评/rollout 后的结果 CSV；提供后 HTML 会展示答案和分数。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/../.." && pwd)"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/outputs/run_$RUN_ID}"

TRAIN_FILE="${TRAIN_FILE:-$REPO_DIR/data/training_data/rl_training_data_v2_mix_grpo_clean_with_context.jsonl}"
TARGET_COUNT="${TARGET_COUNT:-10}"
CANDIDATE_LIMIT="${CANDIDATE_LIMIT:-60}"
SAMPLES_PER_QUERY="${SAMPLES_PER_QUERY:-4}"
MODEL="${MODEL:-ep-20260225140859-njzr9}"
MAX_MATERIAL_CHARS="${MAX_MATERIAL_CHARS:-9000}"
SIMILARITY_THRESHOLD="${SIMILARITY_THRESHOLD:-0.92}"
LINE_NOS="${LINE_NOS:-}"
DOWNLOAD_SOURCE_TERMS="${DOWNLOAD_SOURCE_TERMS:-0}"
ONLINE_KG_PREFLIGHT="${ONLINE_KG_PREFLIGHT:-1}"
WIKIDATA_TIMEOUT="${WIKIDATA_TIMEOUT:-8}"
IGNORE_PROXY_FOR_WIKIDATA="${IGNORE_PROXY_FOR_WIKIDATA:-1}"
WIKIDATA_SEARCH_LIMIT="${WIKIDATA_SEARCH_LIMIT:-3}"
WIKIDATA_MAX_NEIGHBORS_PER_PROP="${WIKIDATA_MAX_NEIGHBORS_PER_PROP:-3}"
RESULT_CSV="${RESULT_CSV:-}"

mkdir -p "$OUTPUT_DIR" "$ROOT_DIR/open_sources/domain_overlay_cache/wikidata" "$ROOT_DIR/open_sources/raw"

echo "[pipeline2-v3] output_dir=$OUTPUT_DIR"

echo "[pipeline2-v3] step1 build permanent code-table KG"
python "$ROOT_DIR/scripts/step1_build_code_table_kg.py" \
  --sources "$ROOT_DIR/data/open_source_manifest.jsonl" \
  --types "$ROOT_DIR/data/entity_types.jsonl" \
  --properties "$ROOT_DIR/data/property_axes.jsonl" \
  --kg-output "$OUTPUT_DIR/step1.permanent_code_table_kg.json" \
  --html-output "$OUTPUT_DIR/step1.permanent_code_table_kg.html"

echo "[pipeline2-v3] step2 build open-KB domain overlays"
overlay_args=()
if [[ "$DOWNLOAD_SOURCE_TERMS" == "1" ]]; then
  overlay_args+=(--download-source-terms)
fi
if [[ "$IGNORE_PROXY_FOR_WIKIDATA" == "1" ]]; then
  overlay_args+=(--ignore-proxy-for-wikidata)
fi
if [[ "$ONLINE_KG_PREFLIGHT" == "1" ]]; then
  echo "[pipeline2-v3] preflight online KG: Wikidata"
  preflight_args=()
  if [[ "$IGNORE_PROXY_FOR_WIKIDATA" == "1" ]]; then
    preflight_args+=(--ignore-proxy)
  fi
  if python "$ROOT_DIR/scripts/check_online_kg.py" \
    --term "bank branch" \
    --limit 1 \
    --timeout "$WIKIDATA_TIMEOUT" \
    --methods "curl" \
    --fail-on-unavailable \
    "${preflight_args[@]}" > "$OUTPUT_DIR/step2.online_kg_preflight.json"; then
    echo "[pipeline2-v3] online KG available"
  else
    echo "[pipeline2-v3] online KG unavailable; use cached/offline domain overlays"
    overlay_args+=(--disable-wikidata-online)
  fi
fi
python "$ROOT_DIR/scripts/step0_build_domain_overlays_from_open_kb.py" \
  --seed-jsonl "$ROOT_DIR/data/open_kb_domain_seed.jsonl" \
  --output-jsonl "$OUTPUT_DIR/step2.open_kb_domain_overlays.jsonl" \
  --cache-dir "$ROOT_DIR/open_sources/domain_overlay_cache/wikidata" \
  --source-cache-dir "$ROOT_DIR/open_sources/raw" \
  --wikidata-search-limit "$WIKIDATA_SEARCH_LIMIT" \
  --wikidata-timeout "$WIKIDATA_TIMEOUT" \
  --wikidata-max-neighbors-per-prop "$WIKIDATA_MAX_NEIGHBORS_PER_PROP" \
  "${overlay_args[@]}"

echo "[pipeline2-v3] step3 synthesize and filter boundary queries"
if [[ -z "${ARK_API_KEY:-}" ]]; then
  echo "[pipeline2-v3] ARK_API_KEY is required for step3" >&2
  exit 2
fi
line_args=()
if [[ -n "$LINE_NOS" ]]; then
  line_args+=(--line-nos "$LINE_NOS")
fi
python "$ROOT_DIR/scripts/step3_synthesize_boundary_queries_from_train.py" \
  --train-file "$TRAIN_FILE" \
  --types "$ROOT_DIR/data/entity_types.jsonl" \
  --properties "$ROOT_DIR/data/property_axes.jsonl" \
  --domain-overlays "$OUTPUT_DIR/step2.open_kb_domain_overlays.jsonl" \
  --output-jsonl "$OUTPUT_DIR/step3.boundary_queries.jsonl" \
  --output-md "$OUTPUT_DIR/step3.boundary_queries.md" \
  --rejected-jsonl "$OUTPUT_DIR/step3.rejected_queries.jsonl" \
  --model "$MODEL" \
  --max-material-chars "$MAX_MATERIAL_CHARS" \
  --target-count "$TARGET_COUNT" \
  --candidate-limit "$CANDIDATE_LIMIT" \
  --similarity-threshold "$SIMILARITY_THRESHOLD" \
  "${line_args[@]}"

echo "[pipeline2-v3] step4 build evaluation dataset"
python "$ROOT_DIR/scripts/step4_build_eval_dataset_from_v3_queries.py" \
  --input "$OUTPUT_DIR/step3.boundary_queries.jsonl" \
  --output-csv "$OUTPUT_DIR/step4.eval_dataset.csv" \
  --output-xlsx "$OUTPUT_DIR/step4.eval_dataset.xlsx" \
  --samples-per-query "$SAMPLES_PER_QUERY"

echo "[pipeline2-v3] step5 build HTML report"
report_args=()
if [[ -n "$RESULT_CSV" ]]; then
  report_args+=(--result-csv "$RESULT_CSV")
  report_args+=(--good-case-html "$OUTPUT_DIR/step5.good_cases.html")
fi
python "$ROOT_DIR/scripts/step5_build_v3_report_html.py" \
  --queries-jsonl "$OUTPUT_DIR/step3.boundary_queries.jsonl" \
  --eval-csv "$OUTPUT_DIR/step4.eval_dataset.csv" \
  --output-html "$OUTPUT_DIR/step5.report.html" \
  "${report_args[@]}"

echo "[pipeline2-v3] done"
echo "[pipeline2-v3] eval_xlsx=$OUTPUT_DIR/step4.eval_dataset.xlsx"
echo "[pipeline2-v3] report_html=$OUTPUT_DIR/step5.report.html"
if [[ -n "$RESULT_CSV" ]]; then
  echo "[pipeline2-v3] good_case_html=$OUTPUT_DIR/step5.good_cases.html"
fi

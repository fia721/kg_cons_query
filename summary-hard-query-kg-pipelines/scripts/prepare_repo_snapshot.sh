#!/usr/bin/env bash
# Export a clean repository snapshot without large training data or generated outputs.
#
# Usage:
#   bash scripts/prepare_repo_snapshot.sh /tmp/summary-hard-query-kg-pipelines

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/prepare_repo_snapshot.sh <target_dir>" >&2
  exit 2
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$1"

mkdir -p "$TARGET_DIR"

rsync -a \
  --delete \
  --delete-excluded \
  --exclude '.git/' \
  --exclude '.agents/' \
  --exclude '.codex/' \
  --exclude 'summary_task_total_log.md' \
  --exclude 'data/' \
  --exclude 'qa_agentic/' \
  --exclude 'artifacts/summary_task_total_log.before_date_reorder_20260629.md' \
  --exclude 'artifacts/cases/' \
  --exclude 'artifacts/kg/' \
  --exclude 'artifacts/query_synthesis/' \
  --exclude 'artifacts/tables/' \
  --exclude 'artifacts/reports/' \
  --exclude 'artifacts/scripts/' \
  --exclude 'artifacts/code_table_pipeline/' \
  --exclude 'artifacts/code_table_pipeline_v2/' \
  --exclude 'artifacts/kg_query_pipeline/scripts/llm_config.jsonl' \
  --exclude 'artifacts/**/outputs/' \
  --exclude 'artifacts/**/__pycache__/' \
  --exclude 'artifacts/**/open_sources/raw/' \
  --exclude 'artifacts/**/open_sources/domain_overlay_cache/' \
  --exclude 'artifacts/**/open_sources/domain_vocab/raw/' \
  --exclude 'artifacts/**/open_sources/*/raw/' \
  --exclude '*.xlsx' \
  --exclude '*.csv' \
  --exclude '*.html' \
  --exclude '*.log' \
  --exclude '*.nohup.log' \
  "$SRC_DIR/" "$TARGET_DIR/"

cat > "$TARGET_DIR/.repo_snapshot_note.md" <<'NOTE'
# Repo Snapshot

This directory was exported from `kg_build_data` with generated outputs and large data excluded.

Before pushing:

```bash
git init
git checkout -b main
git add .
git commit -m "init summary hard query kg pipelines"
git remote add origin https://code.byted.org/users/yuantongfei/summary-hard-query-kg-pipelines.git
git push -u origin main
```
NOTE

cat > "$TARGET_DIR/artifacts/kg_query_pipeline/scripts/llm_config.example.jsonl" <<'NOTE'
{"name":"seed2-lite","model":"ep-REPLACE_ME","api_key_env":"ARK_API_KEY","notes":"Copy to llm_config.jsonl locally if needed. Never commit real keys."}
NOTE

echo "snapshot_dir=$TARGET_DIR"

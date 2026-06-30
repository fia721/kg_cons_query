# 产物索引

## 根目录

- `README.md`：项目入口。
- `STATUS.md`：当前状态和交接信息。
- `summary_task_total_log.md`：总日志，按时间记录重要决策和产物。

## Pipeline1

- 代码：`artifacts/kg_query_pipeline/scripts/`
- 文档：`artifacts/kg_query_pipeline/README.md`
- 输出：`artifacts/kg_query_pipeline/outputs/`

重点输出：

- `run_20260626_170352_filtered7x4/step3.eval_dataset_7q_x4.xlsx`
- `run_20260628_161104/【YTF】contextRL- test- query- 9Q.csv`
- `full_*_pipeline1_rewrite_v1/`：全量训练数据相关输出。

## Pipeline2 V3

- 代码：`artifacts/code_table_pipeline_v3/scripts/`
- 文档：`artifacts/code_table_pipeline_v3/README.md`
- 数据配置：`artifacts/code_table_pipeline_v3/data/`
- 报告：`artifacts/code_table_pipeline_v3/reports/`
- 输出：`artifacts/code_table_pipeline_v3/outputs/`

重点输出：

- `outputs/run_20260630_v3_onlinekg_walk_10case/`
- `outputs/run_20260630_v3_5domains_10x4/`
- `outputs/domain_grounding_tests/`
- `outputs/spath_rag_demo/`

## 早期分析

- bad cases：`artifacts/cases/`
- KG 抽取实验：`artifacts/kg/`
- query synthesis 对比：`artifacts/query_synthesis/`
- 报告：`artifacts/reports/`

## 不建议提交到远端

- `data/`
- `artifacts/**/outputs/`
- `artifacts/**/open_sources/raw/`
- `artifacts/**/open_sources/domain_overlay_cache/`
- 大型 CSV/XLSX/HTML/log。


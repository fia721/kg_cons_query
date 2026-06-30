# Pipeline2 V3：码表 / KG 边界游走

## 位置

`artifacts/code_table_pipeline_v3/`

## 设计原则

1. 永久 KG 只保存 type/property/value，不保存训练材料实体。
2. 材料实体只作为临时 instance graph。
3. 属性轴必须独立可交叉，例如 `industry`、`region`、`org_form`、`hierarchy_level`。
4. negative 必须是当前属性轴下的不同值，而不是现实世界中可重叠的能力标签。
5. 在线 KG 只提供候选路径，最终要经过 S-Path / verifier 过滤。

## 核心模块

### 基础码表

- `data/entity_types.jsonl`
- `data/property_axes.jsonl`
- `scripts/step1_build_code_table_kg.py`

### 领域 overlay

- `data/open_kb_domain_seed.jsonl`
- `scripts/step0_build_domain_overlays_from_open_kb.py`
- 输出：`step2.open_kb_domain_overlays.jsonl`

### S-Path-RAG 风格路径筛选

- `scripts/spath_rag_component.py`
- `scripts/run_spath_rag_demo.py`

作用：

- 候选路径生成。
- 语义加权。
- 泛化 anchor penalty。
- beam/k-shortest 风格排序。

### 专用领域词表

- `data/domain_vocab_source_specs.jsonl`
- `data/domain_vocab_bootstrap_terms.jsonl`
- `scripts/step0_download_domain_vocab_sources.py`
- `scripts/domain_grounding_router.py`
- `scripts/build_domain_vocab_overlay.py`

当前状态：

- 下载源已配置。
- 由于 devbox DNS 失败，官方源暂未下载成功。
- bootstrap terms 已能让 `贷款额度`、`海外发货仓` 走通属性轴路径。

## 常用命令

完整 Pipeline2 V3：

```bash
ARK_API_KEY=... \
RUN_ID=demo_v3_10x4 \
TARGET_COUNT=10 \
CANDIDATE_LIMIT=80 \
SAMPLES_PER_QUERY=4 \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

下载领域词表：

```bash
python artifacts/code_table_pipeline_v3/scripts/step0_download_domain_vocab_sources.py \
  --timeout 30 \
  --retries 1 \
  --ignore-proxy
```

测试 grounding：

```bash
python artifacts/code_table_pipeline_v3/scripts/domain_grounding_router.py \
  --term '贷款额度' \
  --term '海外发货仓' \
  --output-json artifacts/code_table_pipeline_v3/outputs/domain_grounding_tests/grounding.json \
  --output-md artifacts/code_table_pipeline_v3/outputs/domain_grounding_tests/grounding.md
```

测试 S-Path：

```bash
python artifacts/code_table_pipeline_v3/scripts/build_domain_vocab_overlay.py \
  --output-jsonl artifacts/code_table_pipeline_v3/open_sources/domain_vocab/index/domain_vocab_overlay.jsonl

python artifacts/code_table_pipeline_v3/scripts/run_spath_rag_demo.py \
  --graph artifacts/code_table_pipeline_v3/open_sources/domain_vocab/index/domain_vocab_overlay.jsonl \
  --case '贷款额度::银行卡额度::金融额度边界' \
  --case '海外发货仓::履约中心::物流设施边界' \
  --output-json artifacts/code_table_pipeline_v3/outputs/domain_grounding_tests/spath.json \
  --output-md artifacts/code_table_pipeline_v3/outputs/domain_grounding_tests/spath.md
```

## 重要报告

- `artifacts/code_table_pipeline_v3/reports/pipeline2_v3_overview.md`
- `artifacts/code_table_pipeline_v3/reports/permanent_code_table_vs_instance_graph.md`
- `artifacts/code_table_pipeline_v3/reports/online_kg_walk_strategy_research.md`
- `artifacts/code_table_pipeline_v3/reports/domain_vocab_sources_2026_research.md`
- `artifacts/code_table_pipeline_v3/reports/spath_rag_component_note.md`


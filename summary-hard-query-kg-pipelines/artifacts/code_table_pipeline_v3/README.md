# Pipeline2 V3：实体-类型-独立属性 KG

这个目录用于重构码表 pipeline。

V2 的问题是：很多属性轴实际是某个上位词内部的细分，例如 `organization.kind`、`software.module_kind`。这样会导致属性粒度越来越细，接近“一个 case 新增一个属性”。

V3 的正式结构改为：

- 上位类型：`organization`、`person`、`product.software`、`content.template`、`building`、`permission` 等。
- 独立属性轴：`industry`、`region`、`org_form`、`hierarchy_level`、`actor_role`、`relation`、`status`、`capability`、`format`、`usage_purpose`、`time_scope`、`version`。

训练材料里的实体不进入永久 KG。实体只在处理单条召回材料时临时抽取成 instance graph，并链接到永久码表中的 type/property/value。

候选 query 的构造基础不是“目标属性的实体有哪些”，而是：

1. 找到共享锚点属性，例如 `industry=education`。
2. 找到差异目标属性，例如 `org_form=company` vs `org_form=school/training_institution`。
3. 只在材料中 positive 和 negative 都出现时构造 query/rubric。

## 输入文件

- `data/open_source_manifest.jsonl`：公开本体/标准来源清单。
- `data/entity_types.jsonl`：实体上位类型。
- `data/property_axes.jsonl`：可交叉的独立属性轴。
- `data/entities_seed.jsonl`：初始实体断言。

## 输出文件

- `outputs/permanent_code_table_kg.json`
- `outputs/permanent_code_table_kg.html`
- `outputs/entity_cross_property_kg.json`
- `outputs/entity_cross_property_kg.html`
- `outputs/entity_cross_property_candidates.jsonl`

## 启动命令

完整 Pipeline2 V3：

```bash
ARK_API_KEY=... \
RUN_ID=demo_v3_10x4 \
TARGET_COUNT=10 \
CANDIDATE_LIMIT=80 \
SAMPLES_PER_QUERY=4 \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

总体说明见：

- `reports/pipeline2_v3_overview.md`

该入口会依次执行：

1. 基础永久码表 KG。
2. Open-KB-backed 专用领域 overlay KG。
3. 边界 query/rubric 合成与过滤。
4. 测评集 CSV/XLSX 构造。
5. HTML 报告生成。

```bash
bash artifacts/code_table_pipeline_v3/scripts/run_v3_build_code_table_kg.sh
```

从训练数据中抽取临时实体边界并合成 query：

```bash
ARK_API_KEY=... python artifacts/code_table_pipeline_v3/scripts/step3_synthesize_boundary_queries_from_train.py \
  --train-file data/training_data/rl_training_data_v2_mix_grpo_clean_with_context.jsonl \
  --types artifacts/code_table_pipeline_v3/data/entity_types.jsonl \
  --properties artifacts/code_table_pipeline_v3/data/property_axes.jsonl \
  --output-jsonl artifacts/code_table_pipeline_v3/outputs/train_boundary_query_samples.jsonl \
  --output-md artifacts/code_table_pipeline_v3/outputs/train_boundary_query_samples.md
```

调试示例图：

```bash
bash artifacts/code_table_pipeline_v3/scripts/run_v3_build_cross_property_kg.sh
```

## 关键约束

- 多属性实体不能作为互斥 negative。例如一个公司既是电商又是消费零售时，不能因为它拥有电商属性就把它当作消费零售的负例。
- negative 的含义是“当前材料或当前码表断言下不属于目标属性值”，不是“现实世界中永远不可能属于目标属性值”。
- query 应避免显式给出材料范围，例如“这份材料中”；也应避免过度提示领域边界，例如“在企业人事系统语境中”。
- 处理训练数据时可以建议补充 type/property/value，但不允许把材料实体作为永久码表节点。
- 合成 query 时必须显式写出上位词或范围，例如“教育类企业”“金融机构中的支行/网点”“支持导出转换的模板”，避免开放泛问。

## 文件定位

- `step1_build_code_table_kg.py` 是正式永久码表构建脚本。
- `step1_build_cross_property_kg.py` 只是用 `entities_seed.jsonl` 做 smoke test，验证候选挖掘逻辑。
- `entities_seed.jsonl` 不是永久实体库。

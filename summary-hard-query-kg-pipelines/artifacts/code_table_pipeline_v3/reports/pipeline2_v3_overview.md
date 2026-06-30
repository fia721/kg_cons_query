# Pipeline2 V3 总体说明

## 目标

Pipeline2 V3 用来从训练数据的召回材料中自动构造 summary GRPO 边界判别题。

核心目标不是复用原始 query，而是识别召回材料中“同主题、相邻属性、容易被 summary 模型混入”的实体或概念边界，再构造 query、rubric 和测评集。

典型边界包括：

- `教育行业` 锚点下的 `企业 / 学校 / 培训机构 / 教育产品`。
- `金融银行` 锚点下的 `地方银行主体 / 支行或网点 / 总行或集团`。
- 同名软件能力在不同产品线或模块下的归属差异。
- 同一权限能力下的有权限角色、无权限角色、专属权限持有者。

## 设计原则

1. 永久 KG 不存训练材料实体。
   - 永久 KG 只存公开来源、上位类型、属性轴、属性值。
   - 训练材料里的机构、人、功能名、模板名等实体只作为单条材料里的临时 instance graph。

2. 属性轴必须能和上位类型交叉。
   - 好的属性轴是 `industry`、`region`、`org_form`、`hierarchy_level`、`actor_role`、`status`、`capability`。
   - 不再把“某个上位词内部非常细的分类”当作全局属性轴。

3. query 必须清楚，但不能暴露材料范围。
   - 可以问“教育类企业有哪些”“哪些机构属于地方银行主体”“哪些 favourite 属于抖音推荐精排模型的预测目标”。
   - 不应问“这份材料中有哪些”“根据上文”“在某某语境中”。

4. negative 不是现实世界互斥。
   - negative 的含义是“当前材料和当前 query 边界下不应作为目标答案混入”。
   - 例如一个商家现实中可能同时满足多个模式，但如果 query 问的是材料中某个栏目归属，其他栏目下的商家类型就是当前题目的 negative。

5. 专用领域边界优先使用公开 KG 辅助。
   - 对“支行 vs 地方银行主体”这类隐含专业边界，不能只靠字符串或泛化属性。
   - V3 引入 domain overlay KG，用 Wikidata 在线 grounding 和公开 schema/ontology 引用增强边界判断。

## 数据流

```text
训练数据 JSONL
  └─ 召回材料
      ├─ 基础永久码表 KG
      │   ├─ source
      │   ├─ type
      │   ├─ property
      │   └─ property value
      ├─ 专用领域 overlay KG
      │   ├─ domain concept
      │   ├─ concept relation
      │   ├─ query pattern
      │   └─ open-KB references
      └─ LLM 临时实例图抽取
          ├─ temporary_instance_entities
          ├─ linked_types
          ├─ linked_properties
          └─ evidence
              ↓
        边界 KG 引用与游走
          ├─ anchor_properties
          ├─ target_property
          ├─ negative_properties
          ├─ domain_walk
          └─ why_summary_model_gets_confused
              ↓
        query / rubric 合成与过滤
          ├─ synthesized_query
          ├─ positive_rubric
          ├─ negative_rubric
          ├─ zero_score_conditions
          └─ rejected_queries
              ↓
        测评集 CSV/XLSX
              ↓
        rollout / judge 结果
              ↓
        HTML 报告
```

## Step 1：基础永久码表 KG

脚本：

- `artifacts/code_table_pipeline_v3/scripts/step1_build_code_table_kg.py`

输入：

- `data/open_source_manifest.jsonl`
- `data/entity_types.jsonl`
- `data/property_axes.jsonl`

输出：

- `step1.permanent_code_table_kg.json`
- `step1.permanent_code_table_kg.html`

永久 KG 节点类型：

- `source`：公开来源或标准。
- `type`：实体上位类型，例如组织、人物、软件产品、模板、权限。
- `property`：独立属性轴，例如行业、地区、组织形态、层级、角色、状态、能力。
- `value`：属性值，例如 `industry=education`、`org_form=school`、`hierarchy_level=branch`。

## Step 2：专用领域 Open-KB Overlay

脚本：

- `artifacts/code_table_pipeline_v3/scripts/step0_build_domain_overlays_from_open_kb.py`

输入：

- `data/open_kb_domain_seed.jsonl`

输出：

- `step2.open_kb_domain_overlays.jsonl`

当前默认策略：

- 先用 `check_online_kg.py` 做 Wikidata 在线 KG preflight，默认绕过 `http_proxy/https_proxy`。
- 如果在线 KG 可用，Wikidata 使用在线 API，并缓存 search/entity JSON。
- 如果在线 KG 不可用，主流程自动切换到已有缓存/seed overlay，不同步请求 Wikidata，避免 pipeline 卡住。
- Schema.org、DBpedia、NAICS、NIST RBAC 默认只使用本地已有缓存和 URL 引用。
- 如果设置 `DOWNLOAD_SOURCE_TERMS=1`，才下载公开源原始文件并做 term 匹配。

这样可以避免每次 pipeline 都等待大文件下载，同时保留公开来源 grounding。

overlay 中的关键字段：

- `concepts`：领域概念，例如 `banking.local_bank`、`banking.branch`。
- `maps_to`：映射到基础码表中的 type/property/value。
- `relations`：概念间关系，例如 `branch subunit_of local_bank`、`local_bank contrast_with branch`。
- `query_patterns`：领域内推荐 query 形态和 rubric 注意事项。
- `open_kb_refs`：Wikidata / Schema.org / DBpedia / NIST 等公开来源引用。

## Step 3：边界 Query 合成与过滤

脚本：

- `artifacts/code_table_pipeline_v3/scripts/step3_synthesize_boundary_queries_from_train.py`

输入：

- 训练数据 JSONL。
- 基础 type/property 码表。
- Step 2 的 domain overlay。

输出：

- `step3.boundary_queries.jsonl`
- `step3.boundary_queries.md`
- `step3.rejected_queries.jsonl`

LLM 需要输出：

- `temporary_instance_entities`：当前材料里的临时实体。
- `boundary.upper_type`：一级类型。
- `boundary.anchor_properties`：锚点属性。
- `boundary.target_property`：目标属性。
- `boundary.negative_properties`：负向属性。
- `boundary.domain_walk`：命中的领域 KG 概念和游走关系。
- `synthesized_query`：最终 query。
- `machine_eval_guidance`：机评引导。

过滤规则：

- query 不能包含“这份材料”“召回材料”“上文”等。
- query 与原始 query 的字符相似度不能超过阈值，默认 `0.92`。
- 必须有至少 1 个 positive entity 和 1 个 negative entity。
- 必须有 `target_property` 和 `anchor_properties`。

## Step 4：测评集构造

脚本：

- `artifacts/code_table_pipeline_v3/scripts/step4_build_eval_dataset_from_v3_queries.py`

输入：

- Step 3 的 `step3.boundary_queries.jsonl`

输出：

- `step4.eval_dataset.csv`
- `step4.eval_dataset.xlsx`

输出字段：

- `dataID`
- `query`
- `企业内是否有知识`
- `预期答复（机评文本）`
- `ref图片文件名称`
- `机评忽略case`

每条 query 会按 `SAMPLES_PER_QUERY` 重复，方便 rollout 多次采样。

## Step 5：HTML 报告

脚本：

- `artifacts/code_table_pipeline_v3/scripts/step5_build_v3_report_html.py`

输入：

- Step 3 query JSONL。
- Step 4 eval CSV。
- 可选 rollout/judge 结果 CSV。

输出：

- `step5.report.html`

报告结构：

1. 原始 query / 重构 query。
2. 材料中的一级类型、锚点属性、目标属性、负向属性。
3. positive/negative 边界。
4. domain overlay KG 的引用和游走信息。
5. 机评引导。
6. 如果提供结果 CSV，展示每次采样的分数、回答、评测理由、召回材料摘录，以及基于字符串的正负边界命中辅助检查。

## 一键启动

脚本：

- `artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh`

示例：

```bash
cd /mlx_devbox/users/yuantongfei/playground/kg_build_data

ARK_API_KEY='你的 key' \
RUN_ID=demo_v3_10x4 \
TARGET_COUNT=10 \
CANDIDATE_LIMIT=80 \
SAMPLES_PER_QUERY=4 \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

指定训练数据：

```bash
ARK_API_KEY='你的 key' \
TRAIN_FILE=data/training_data/rl_training_data_v2_mix_grpo_clean_with_context.jsonl \
RUN_ID=train_v3_10x4 \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

只跑指定行号：

```bash
ARK_API_KEY='你的 key' \
LINE_NOS=11,37,65,80,111,211,311,411,711,1111 \
RUN_ID=v3_fixed_lines \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

强制下载公开源 term：

```bash
ARK_API_KEY='你的 key' \
DOWNLOAD_SOURCE_TERMS=1 \
RUN_ID=v3_with_source_download \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

单独检查在线 KG：

```bash
python artifacts/code_table_pipeline_v3/scripts/check_online_kg.py \
  --term "bank branch" \
  --limit 1 \
  --timeout 20 \
  --methods urllib,requests,curl
```

当前 devbox 环境里默认有 `http_proxy/https_proxy=sys-proxy-rd-relay.byted.org:8118`。走该代理访问 Wikidata 会出现 SSL handshake/proxy timeout；显式绕过代理后，Wikidata Search + EntityData 可以连通。SPARQL 端点可能返回 429 限流，因此主流程优先使用 Search + EntityData，不把 SPARQL 作为高频依赖。

已有 rollout/judge 结果后生成 HTML：

```bash
RESULT_CSV=path/to/result.csv \
RUN_ID=已有run_id \
bash artifacts/code_table_pipeline_v3/scripts/run_pipeline2_v3.sh
```

如果只想单独重新生成 HTML：

```bash
python artifacts/code_table_pipeline_v3/scripts/step5_build_v3_report_html.py \
  --queries-jsonl artifacts/code_table_pipeline_v3/outputs/run_xxx/step3.boundary_queries.jsonl \
  --eval-csv artifacts/code_table_pipeline_v3/outputs/run_xxx/step4.eval_dataset.csv \
  --result-csv path/to/result.csv \
  --output-html artifacts/code_table_pipeline_v3/outputs/run_xxx/step5.report.html
```

## 当前局限

- HTML 中的 rollout 命中辅助检查是轻量字符串匹配，只用于人工定位；正式判断仍应依赖 judge 模型。
- domain overlay 的质量依赖 seed 和 Wikidata grounding。后续可以继续扩展金融、教育、软件、权限之外的专用领域。
- 如果公开源大文件下载网络不稳定，建议保持默认在线 Wikidata + 本地缓存模式。

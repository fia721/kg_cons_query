# KG Query Pipeline

这个目录把当前“从训练召回材料合成 hard query/rubric，再构造测评集并分析测评结果”的流程整理成一个可复用的小仓库。

## 流程

1. `step1_extract_train_materials.py`
   - 输入训练 JSONL。
   - 读取每条训练样本的 tool/context 召回材料。
   - 输出标准化 materials JSONL。

2. `step2_synthesize_query_rubric.py`
   - 输入 step1 的 materials JSONL。
   - 调用 LLM 识别大主题、小属性、positive/negative 对比集合、召回设计，并生成 query/rubric。
   - 支持三类 query：
     - `fine_grained_attribute`：细分属性判别题，优先生成。
     - `enumeration_filter`：集合过滤题。
     - `direct_attribute`：直接属性题。
   - 判断是否可以合成：必须有 positive boundary、negative boundary，且召回对比概率不能为 low。
   - 计算改写 query 与原始 query 的相似度；相似度过高则丢弃。
   - 输出 accepted query/rubric JSONL；rejected/error 样本写入 `.rejected` JSONL。

3. `step2b_filter_semantic_similarity.py`
   - 输入 step2 的 raw query/rubric JSONL。
   - 调用 LLM 判断合成 query 与原始 user query 是否是同一语义任务。
   - 如果只是同义改写、答案目标高度重合，丢弃。
   - 如果只是主题相关但目标属性/答案集合/边界不同，保留。
   - 输出语义过滤后的正式 query/rubric JSONL；语义相似被拒样本写入 `.semantic_rejected` JSONL。

4. `step3_build_eval_dataset.py`
   - 输入 step2 的 query/rubric JSONL。
   - 构造测评集 CSV/XLSX，支持每个 query 重复 N 条作为多次采样任务。
   - 输出测评集。

5. `step4_build_eval_report_html.py`
   - 输入 step2 query/rubric、step3 测评集、评测返回 CSV 目录。
   - 汇总 query、rubric、召回、答案、分数，生成 HTML。

6. `step5_audit_retrieval_alignment.py`
   - 输入 step2 query/rubric 和评测返回 CSV 目录。
   - 审计从头召回里是否命中 positive/negative，以及答案是否纳入 negative。
   - 输出 Markdown 报告。

7. `step0_run_pipeline.py`
   - 串起 step1-step3，中间包含 step2b 语义相似度过滤。
   - 默认先合成 `synth_limit * 2` 个 raw query/rubric 候选，再语义过滤到 10 个 query/rubric，并构造 30 条测评集。

## 本轮产物

默认输出目录：

`artifacts/kg_query_pipeline/outputs/run_YYYYmmdd_HHMMSS/`

关键文件：

- `step1.materials.jsonl`
- `step2.query_rubrics.raw.jsonl`
- `step2.query_rubrics.raw.jsonl.rejected`
- `step2.query_rubrics.jsonl`
- `step2.query_rubrics.jsonl.semantic_rejected`
- `step3.eval_dataset.csv`
- `step3.eval_dataset.xlsx`

## 说明

默认配置：

- 候选训练样本：`--candidate-limit 30`
- 接受 query 数：`--synth-limit 10`
- 测评集：`--eval-query-limit 10 --samples-per-query 3`
- query 相似度过滤阈值：`--similarity-threshold 0.85`

运行示例：

```bash
python artifacts/kg_query_pipeline/scripts/step0_run_pipeline.py \
  --synth-limit 10 \
  --eval-query-limit 10 \
  --samples-per-query 3
```

过滤规则：

- 无 query、无 positive boundary、无 negative boundary：丢弃。
- `can_synthesize=false`：丢弃。
- `retrieval_contrast_likelihood=low`：丢弃。
- positive/negative evidence strength 为 weak：丢弃。
- 改写 query 与原始 query 相似度 `>= 0.85`：丢弃。
- LLM 判断合成 query 与原始 query 语义相同或高度近似：丢弃。
- `quality_checks.query_target_consistency != pass`：丢弃。query 本身必须能公平推出目标属性，不能依赖隐藏 rubric。
- `quality_checks.query_disambiguation_sufficient=false`：丢弃。多义词/多语境术语必须有足够锚点。
- `quality_checks.negative_in_query_scope=false`：丢弃。negative 必须在 query 语义下确实不应作为答案。
- 开放枚举 query 若 `positive_set_completeness=partial/unknown`：丢弃。不能用不完整 positive 列表做闭集判分。
- `expected_summary_error_likelihood=low`：丢弃。边界正确但太容易、预期没有 summary 错误方差的样本不进入测试集。
- 当 `query_type=fine_grained_attribute` 时，必须满足：
  - 输出显式 `taxonomy`。
  - `taxonomy.mutual_exclusivity=exclusive`，二级类别必须是同一实体在该属性轴下通常只能属于一个类别的互斥属性。
  - 至少两个二级类别各自包含 2 个及以上实体。
  - positive 和 negative 分别来自这些多实体类别。
  - 如果二级类别只是能力、资质、经验、渠道、意向等可重叠标签，或只是材料表格中的栏目归属，则丢弃，不作为 fine-grained 属性判别题。
- 当 `query_type=enumeration_filter` 时，必须满足：
  - 至少一个 hard negative 类别包含 2 个及以上实体。
  - 如果负例类别只有 1 个实例，丢弃；这类样本 hard negative 太薄，不足以训练稳定边界。

## Fine-Grained Attribute

现在 step2 prompt 显式要求优先合成 `fine_grained_attribute`。这类题的目标不是简单枚举“哪些”，而是让模型分清同一大主题下的细分属性。

典型模式：

- 教育类机构中区分“学校”和“培训机构/企业”，query 可问：`教育类企业有哪些？`
- 公司相关人物中区分“员工”和“报道作者/记者”，query 可问：`豌豆荚的工作人员有哪些？`

不合格模式：

- 只有 1 个正例实体对比多个负例实体，例如只有 1 个“活动报名系统操作指南”对比短信名单/执行模板。这可以作为普通边界题，但不作为 `fine_grained_attribute`。
- query 过度提示目标语境，例如 `在企业人事系统语境中，ESS属于哪类自助服务功能？`。这会直接排除其他语境，降低迷惑性。
- 二级类别不是互斥属性，例如把“有美国本地仓储物流能力的商家”和“有供应链资源/跨境运营经验的商家”作为正负类。一个商家可以同时具备这些能力，因此不能把后者作为绝对负例；除非 query 明确问“材料中某个栏目列出的适配类型”，否则应丢弃。

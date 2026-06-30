# Pipeline1：材料内主题/属性边界合成

## 位置

`artifacts/kg_query_pipeline/`

## 核心流程

1. `step1_extract_train_materials.py`
   - 从训练 JSONL 中抽取 query、历史 messages、召回材料。

2. `step2_synthesize_query_rubric.py`
   - LLM 抽一级主题、二级属性、positive/negative boundary。
   - 生成 query、positive rubric、negative rubric、机评引导。

3. `step2b_filter_semantic_similarity.py`
   - 用 LLM 判断合成 query 是否和原始 query 过于相似。
   - 太相似则丢弃。

4. `step2c_rewrite_query_surface.py`
   - 后处理 query 表述，降低“xxx 有哪些”的单一模板问题。

5. `step3_build_eval_dataset.py`
   - 每条 query 采样 N 次，生成 CSV/XLSX 测评集。

6. `step4_build_eval_report_html.py`
   - 汇总 query、rubric、召回、答案、分数，生成 HTML。

7. `step5_audit_retrieval_alignment.py`
   - 判断召回材料是否命中 negative，答案是否断言式纳入 negative。

## 当前关键规则

- negative 和 positive 必须在材料中都能被召回。
- fine-grained attribute 类题必须有互斥属性轴。
- 属性不能可重叠。例如“有美国仓储能力”和“有供应链资源”不是互斥。
- query 不能过度提示材料范围，例如“这份材料中”“在某系统语境下”。
- 开放枚举题如果 positive 不完整，不适合闭集判分。

## 常用命令

```bash
python artifacts/kg_query_pipeline/scripts/step0_run_pipeline.py \
  --train-file data/training_data/rl_training_data_v2_mix_grpo_clean_with_context.jsonl \
  --synth-limit 10 \
  --eval-query-limit 10 \
  --samples-per-query 3
```

全量训练数据入口：

```bash
bash artifacts/kg_query_pipeline/scripts/run_pipeline1_full_training_data.sh
```

## 重要产物

- `artifacts/kg_query_pipeline/outputs/run_*/step2.query_rubrics.jsonl`
- `artifacts/kg_query_pipeline/outputs/run_*/step3.eval_dataset.xlsx`
- `artifacts/kg_query_pipeline/outputs/run_*/step4.report.html`
- `artifacts/kg_query_pipeline/outputs/run_*/step5.audit.md`


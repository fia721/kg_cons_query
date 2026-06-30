# S-Path-RAG 组件接入说明

## 代码来源说明

没有找到 S-Path-RAG 的官方 GitHub 或 arXiv code 字段，因此当前组件不是复制官方代码，而是按论文思想实现了 pipeline 需要的可运行版本：

- 候选路径生成
- 语义加权路径搜索
- beam/k-shortest 风格排序
- 泛化 anchor 过滤
- verifier 友好的结构化解释输出

代码位置：

- `scripts/spath_rag_component.py`
- `scripts/run_spath_rag_demo.py`

## 当前输入

支持两类本地产物：

- `step2.open_kb_domain_overlays.jsonl`
- `permanent_code_table_kg.json`

建议优先使用 overlay，因为里面同时有：

- domain
- concept
- property/value
- Wikidata grounding
- online_kg_walks 缓存

## 当前输出

每条路径包含：

- `score`
- `semantic_score`
- `generic_penalty`
- `rejected`
- `reject_reasons`
- `nodes`
- `edges`

## 设计取舍

当前版本没有依赖外部 embedding 或在线 Wikidata，因此能在网络不可用时跑通。

后续可以替换的点：

- `edge_cost` 中的 token overlap scorer 可以替换为 embedding similarity。
- `rejected/reject_reasons` 可以替换为 LLM verifier。
- `GENERIC_ANCHOR_TERMS` 可以接入 Boundary Book / Error Book。


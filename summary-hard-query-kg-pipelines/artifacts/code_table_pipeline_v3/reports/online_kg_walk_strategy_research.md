# 在线 KG 游走策略调研

## 2026 年后资料补充

截至 2026-06-30，严格按 2026 年以后筛选，和我们当前问题最相关的不是传统 KG embedding 或早期 entity set expansion，而是以下几类新 GraphRAG / KG-RAG / agent-native retrieval 工作。

### S-Path-RAG: Semantic-Aware Shortest-Path RAG

- 时间：2026-03
- 链接：https://arxiv.org/abs/2603.23512
- 关键点：
  - 不做无约束一跳游走。
  - 先枚举 bounded-length candidate paths。
  - 用 semantic weighted k-shortest path、beam search、constrained random walk 组合召回。
  - 再用 path scorer、contrastive path encoder、lightweight verifier 过滤路径。
- 对我们的启发：
  - 当前 `学校 -> 建筑物 -> 住宅/车库` 应该在 verifier 阶段被过滤。
  - 我们可以把每条候选游走路径表示成：
    - `source_entity`
    - `anchor`
    - `candidate_sibling`
    - `relation_path`
    - `domain_consistency_score`
    - `boundary_usefulness_score`
  - 再让轻量 LLM/verifier 判断是否符合“同领域、不同属性值、可构造混淆边界”。

### LLM-Wiki: Retrieval as Reasoning

- 时间：2026-05
- 链接：https://arxiv.org/abs/2605.25480
- 关键点：
  - 把 retrieval 从一次性 lookup 改成 agent-native reasoning。
  - 文档被编译成结构化 wiki page 和双向链接。
  - agent 可以 search/read/follow links。
  - 引入 Error Book，持久记录结构和语义错误，用于自修正。
- 对我们的启发：
  - 不要直接信任在线 KG 的原始边。
  - 应该把“错误游走”持久化，例如：
    - `学校 -> 建筑物 -> 住宅` 是错误边界。
    - `教育机构 -> 教育企业/学校/培训机构` 是可用边界。
  - 后续 pipeline 在构造 query 时先查 Error Book / Boundary Book，避免重复犯错。

### Is GraphRAG Needed? Context Optimization

- 时间：2026-06
- 链接：https://arxiv.org/abs/2606.25656
- 关键点：
  - 系统比较 Basic RAG、GraphRAG、Modular RAG、Agentic RAG。
  - 强调 expanded retrieval 不一定带来 generation 质量提升。
  - 提出 context engineering 来压缩 GraphRAG/Agentic RAG 的 token 使用。
- 对我们的启发：
  - 我们现在的问题正是“扩展了图，但扩展出噪声”。
  - pipeline 不应追求更多 KG neighbor，而应追求更强的 context selection。
  - query 构造前应加一个 `context_budgeted_boundary_selection`：
    - 只保留 positive/negative 边界各 2-5 个代表实体。
    - 丢弃与边界无关的邻居路径。

### Agents-K1: Agent-native Knowledge Orchestration

- 时间：2026-06
- 链接：https://arxiv.org/abs/2606.13669
- 关键点：
  - 构建 agent-native scientific KG。
  - schema 不只抽实体和 citation，还抽 claim、evidence、mechanism、method lineage。
  - 训练了信息抽取 backbone，并用 GRPO + rule-based reward。
  - 提供 graphanything CLI，统一 web search、multimodal graph retrieval、cross-document traversal。
- 对我们的启发：
  - 我们的 KG 不应该只做实体-上位词，而应该抽“边界证据”：
    - 这个实体为什么属于 positive 属性？
    - 这个实体为什么属于 negative 属性？
    - 文本里哪句话支持？
  - 对 summary 训练数据构造，更重要的是 `entity -> property_value -> evidence_span`，不是开放 KG 上的远邻实体。

### MixRAGRec: MoE KG-RAG for Recommendation

- 时间：2026-05
- 链接：https://arxiv.org/abs/2605.28175
- 关键点：
  - 不同 query 复杂度需要不同 KG retrieval granularity。
  - 使用 Mixture-of-Experts Retrieval Agent 路由到不同粒度的 KG retrieval expert。
  - 再用 alignment agent 把结构化 KG 转成 LLM 友好的自然语言。
- 对我们的启发：
  - 我们也应该按 query 构造目标选择不同游走粒度：
    - 枚举题：实体集合边界。
    - 细分属性判别题：属性轴边界。
    - 时间/版本/状态题：同主体多状态边界。
  - 不应该所有 case 都用同一种 `shared_anchor` 游走。

## 2026 资料下的策略修正

基于上面几篇 2026 工作，当前 pipeline2_v3 应该从“在线 KG 一跳游走”改成“候选路径生成 + 语义 verifier + 错误记忆”的结构：

1. **候选生成**
   - 从材料实体、本地属性轴、Wikidata anchors 生成候选路径。
   - 不直接使用候选作为 query 边界。

2. **路径级结构过滤**
   - 过滤泛化 anchor：
     - `object`
     - `entity`
     - `building`
     - `geographic object`
     - `organization`，除非当前属性轴就是组织形态。
   - 保留和材料主题一致的 anchor：
     - `education`
     - `finance`
     - `logistics`
     - `software`
     - `role/person_relation`

3. **LLM/verifier 打分**
   - 输入候选路径和 evidence span。
   - 输出结构化字段：
     - `same_domain`
     - `property_axis_clear`
     - `positive_negative_mutually_exclusive_in_material`
     - `likely_to_confuse_summary_model`
     - `reject_reason`

4. **Boundary Book / Error Book**
   - 记录被人工或 judge 判坏的路径。
   - 例如：
     - reject: `学校 -> 建筑物 -> 住宅`
     - accept: `学校 -> 教育组织 -> org_form:school/company/training_institution`

5. **按题型路由**
   - enum query 走集合边界。
   - fine-grained attribute query 走属性轴边界。
   - time/version/status query 走同主体多状态边界。

结论：

- 2026 年后的方向更支持“agentic retrieval + verifier + context optimization”，而不是“找到一个更聪明的一跳游走公式”。
- 对我们最值得实现的是 S-Path-RAG 式的 `candidate path scorer/verifier`，再加 LLM-Wiki 式的 `Boundary Book / Error Book`。

## 背景问题

当前在线 KG 游走采用的是比较裸的 `entity --relation--> anchor <--same relation-- sibling`：

- 例子：`学校 --subclass_of--> 建筑物 <--subclass_of-- 住宅/车库/宫殿`
- 问题：`建筑物` 是高层、泛化、跨领域 anchor，虽然图结构合法，但对 summary 边界构造是噪声。
- 需要的不是“图上相邻”，而是“在目标语义轴上相邻，并且能构成 positive/negative 边界”。

## 调研结论

不建议继续使用无约束一跳 shared-anchor。更合适的路线是组合三类已有策略：

1. **类型/属性约束的 taxonomy sibling finding**
   - 只沿目标属性轴寻找 sibling，例如 `org_form=school/company/training_institution`。
   - anchor 必须满足信息量约束，不能使用 `建筑物`、`地理对象` 这种泛化节点。
   - 对应我们的问题：`教育类企业` 应该从 `industry=education` 这个共享锚点出发，再沿 `org_form` 找 `学校/培训机构/企业`，而不是从 `学校 -> 建筑物` 发散。

2. **Entity Set Expansion with positive/negative auxiliary sets**
   - SetExpan 类方法强调避免 semantic drift，通过特征选择和 rank ensemble 抑制噪声。
   - LM probing 的 set expansion 会显式生成 positive class names 和 negative class names，用来防止扩展到相邻但错误的类。
   - 对应我们的问题：从材料中抽出目标实体集合和干扰实体集合，让模型/算法先确认“目标类名”和“负类名”，再扩展或构造 query。

3. **KG embedding / kNN rerank**
   - 用 KG 结构或文本描述训练/加载 embedding，把候选 sibling 先粗召回，再按 embedding 相似度、类型一致性、属性轴差异重排。
   - 这适合解决 Wikidata 结构噪声：图上一跳合法的节点很多，但 embedding 和类型过滤能把 `住宅/车库` 这种拉低。

## 可复用论文/代码方向

### SetExpan / LM probing set expansion

- 适用点：从少量 seed 实体扩展同类实体，同时避免 semantic drift。
- 可借鉴策略：
  - 不只看图连接，还要选择干净的上下文/属性特征。
  - 显式生成 positive class name 和 negative class name。
  - 用负类名约束扩展，避免“学校 -> 建筑物 -> 车库”。
- 在本项目中的用法：
  - 当前召回材料里先抽出实体和候选属性。
  - 把目标实体当 positive seeds，把相邻干扰实体当 negative seeds。
  - 让 LLM 或 set-expansion scorer 输出：目标类、负类、可用于出题的属性轴。

### TaxoExpan / taxonomy expansion

- 适用点：判断一个概念应该挂到 taxonomy 的哪个 anchor 下。
- 可借鉴策略：
  - anchor 不是随便取一跳父节点，而是预测“直接上位概念”。
  - 使用局部 taxonomy 结构判断 query concept 是否是 anchor concept 的直接下位。
- 在本项目中的用法：
  - 对 Wikidata 返回的多个 anchor 做打分。
  - 只保留“直接语义上位词”，过滤 `建筑物` 这种兼具物理形态但不是当前任务语义轴的 anchor。

### PRA / ProPPR / path-constrained random walk

- 适用点：不是走所有路径，而是走带 relation schema 的路径。
- 可借鉴策略：
  - metapath / relation path 是人为或学习出来的语义模板。
  - 不使用无约束 BFS/random walk。
- 在本项目中的用法：
  - 为每个领域定义合法 metapath：
    - 教育组织：`entity -> industry/domain -> org_form sibling`
    - 金融机构：`entity -> industry=finance -> hierarchy_level/org_form sibling`
    - 电商履约：`entity -> logistics_function -> facility_type/region sibling`
  - 禁止跨到 `physical_object/building/location`，除非 query 明确问地点/设施类型。

### PyKEEN / kNN-KGE

- 适用点：对候选实体做 embedding-based 相似度和邻居检索。
- 可复用代码：
  - PyKEEN：成熟的 KG embedding 训练/评测框架。
  - KNN-KG：用 kNN memory 改善 KG embedding 的邻居推理。
- 在本项目中的用法：
  - 不一定先训练大模型；可以先把 Wikidata/cache + 本地属性轴做小图，用 PyKEEN 训练 TransE/ComplEx。
  - 候选生成仍用 SPARQL/缓存，排序用 embedding + 类型约束 + LLM 判别。

## 推荐替换方案

把当前在线游走拆成 4 层：

1. **候选 anchor 生成**
   - 来源：Wikidata `P31/P279/P452/P361/P1269`、本地码表、材料内实体共现。
   - 不直接用于 query。

2. **anchor 过滤与打分**
   - 过滤规则：
     - 丢弃高泛化 anchor：`实体`、`对象`、`地点`、`建筑物`、`组织` 等。
     - 如果 query/材料目标不是物理设施，不允许走 `building/location/physical_object` 轴。
   - 打分信号：
     - anchor label 是否与材料主题一致。
     - anchor 下 sibling 是否同属一个领域。
     - anchor 是否能导出互斥属性轴。

3. **属性轴游走**
   - 只在明确属性轴上游走：
     - `industry`
     - `org_form`
     - `hierarchy_level`
     - `facility_function`
     - `region`
     - `role_relation`
     - `service_channel`
   - 目标是找 `same domain + different property value`，不是找任意 sibling。

4. **LLM/embedding rerank**
   - 输入候选边界：
     - positive entities
     - negative entities
     - shared upper concept
     - differing property axis
   - 输出是否适合构造 query。
   - 判断标准：
     - positive/negative 是否在材料中都出现。
     - 负例是否容易被模型混入答案。
     - query 不显式暴露材料范围。

## 对当前例子的修正

### 学校

错误路径：

- `学校 -> 建筑物 -> 住宅/车库`

应保留路径：

- `学校 -> 教育机构/教育组织`
- 再结合本地属性轴：
  - `industry=education`
  - `org_form=school`
  - sibling values：`company`、`training_institution`

可构造边界：

- positive：教育行业 + 企业/培训机构
- negative：学校/中学/高校
- query：`教育类企业有哪些？`

### 地方银行

应使用：

- `industry=finance`
- `org_form=bank`
- `hierarchy_level=regional/local bank` vs `branch/outlet`

可构造边界：

- positive：地方银行主体
- negative：支行/网点/分行
- query：`地方银行有哪些？`

### 海外发货仓

应使用：

- `domain=ecommerce/logistics`
- `facility_function=fulfillment/shipping/storage`
- `region=overseas/local/US`

不能只按 `warehouse -> building/facility` 游走。

## 下一步实现建议

1. 在 `debug_external_kg_walk.py` 和正式 pipeline 中加入 anchor denylist / allowlist。
2. 新增 `property_axis_walk.py`：
   - 输入：材料实体 + Wikidata anchors + 本地码表属性轴。
   - 输出：`same_domain_different_property_value` 候选边界。
3. 新增 LLM reranker：
   - 判断候选边界是否能构造 query。
   - 判断 query 是否会召回 positive 和 negative。
4. 只把通过 rerank 的候选送入 query synthesis。

## 参考资料

- SetExpan: Corpus-Based Set Expansion via Context Feature Selection and Rank Ensemble, arXiv:1910.08192, https://arxiv.org/abs/1910.08192
- Empower Entity Set Expansion via Language Model Probing, arXiv:2004.13897, https://arxiv.org/abs/2004.13897
- TaxoExpan: Self-supervised Taxonomy Expansion with Position-Enhanced Graph Neural Network, arXiv:2001.09522, https://arxiv.org/abs/2001.09522
- Efficient Inference and Learning in a Large Knowledge Base / ProPPR, arXiv:1404.3301, https://arxiv.org/abs/1404.3301
- PyKEEN 1.0, arXiv:2007.14175, https://arxiv.org/abs/2007.14175
- kNN-KGE, arXiv:2201.05575, https://arxiv.org/abs/2201.05575

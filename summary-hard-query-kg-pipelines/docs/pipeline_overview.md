# Pipeline 总览

## 背景

summary 任务里，模型容易在召回材料边界处犯错：材料中同时出现相关但属性不同的实体，模型 summary 时把 negative 混入答案。目标是从训练数据和错误 case 中合成这种 hard query，并用 rubric 做自动评测。

## 两条路线

### Pipeline1：材料内边界

核心假设：训练材料本身已经包含 positive 和 negative，只需要把其中的一级主题、二级属性和互斥边界抽出来。

优点：

- 不依赖外部 KG。
- 能直接从训练数据规模化合成。
- 已验证一部分 query 能产生有效 rollout 方差。

缺点：

- 对 user query 难度和材料边界依赖较强。
- LLM 容易构造过宽或过显式的 query。
- 对隐含专业属性识别有限。

### Pipeline2 V3：码表/KG 边界

核心假设：很多混淆点来自实体的细分属性，材料中未必显式写出；需要借助稳定码表、领域词表和路径筛选来找到相邻边界。

优点：

- 能处理专用领域属性，例如金融额度、物流设施、银行层级。
- 能通过属性轴控制互斥关系，减少“可重叠属性当负例”的错误。
- 可与在线词表和 S-Path-RAG verifier 结合。

缺点：

- 工程复杂度高。
- 在线服务受 DNS/代理影响。
- 本地权威词表下载和解析还需要继续完善。

## 推荐组合

1. Pipeline1 先从训练材料中发现显式边界。
2. Pipeline2 V3 对 Pipeline1 的实体和属性做 grounding / path verification。
3. 只有当 positive 和 negative 都能被材料召回，且路径 verifier 判定为同领域不同属性值时，才进入 query/rubric 合成。


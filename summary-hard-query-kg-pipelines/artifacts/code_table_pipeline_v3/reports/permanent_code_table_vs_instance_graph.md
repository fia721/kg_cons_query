# 永久码表 KG 与临时实体图的边界

## 结论

永久码表 KG 不应该把训练材料里的实体作为长期节点保存。

更合理的结构是两层：

1. 永久码表层：公开来源、上位类型、独立属性轴、属性值。
2. 临时实例层：每条召回材料中抽取出来的实体、实体属性、实体关系。

query 构造只需要在当前材料的临时实例层里看实体是否有多边重合、是否存在容易混淆的属性边界；不需要把这些实体写入永久 KG。

## 为什么实体不应永久化

如果把每条训练材料里的实体都长期写入 KG，会带来几个问题：

- 码表会被 case 污染，逐渐退化成实体库。
- 属性轴会被具体实体牵着走，重新出现“一个 case 一个属性”的问题。
- 同名实体、短文本别名、材料内临时概念很难维护生命周期。
- query 生成实际只需要当前材料里的 positive/negative 是否共现，而不是全局实体全集。

## 正确的数据流

永久码表：

```text
source -> type
source -> property_axis
property_axis -> property_value
type -> subtype
```

单条材料处理时：

```text
retrieval materials
  -> extract entities
  -> link entity to type/property/value from permanent code table
  -> build temporary instance graph
  -> find overlapping anchors and target boundaries
  -> synthesize query/rubric
```

## 临时实例图字段

建议后续 step 使用这样的结构：

```json
{
  "record_id": "...",
  "entities": [
    {
      "local_entity_id": "e1",
      "name": "一七一中学",
      "aliases": ["171中学"],
      "linked_types": ["organization.school"],
      "linked_properties": [
        {"property_id": "industry", "value_id": "education", "evidence": "..."},
        {"property_id": "org_form", "value_id": "school", "evidence": "..."}
      ],
      "source_span": "..."
    }
  ],
  "candidate_boundaries": [
    {
      "anchor": {"property_id": "industry", "value_id": "education"},
      "target": {"property_id": "org_form", "value_id": "company"},
      "positive_entity_ids": ["..."],
      "negative_entity_ids": ["..."]
    }
  ]
}
```

## 当前 v3 文件的定位

- `step1_build_code_table_kg.py`：正式的永久码表 KG 构建脚本，不包含实体。
- `step1_build_cross_property_kg.py`：保留为 smoke test / 示例图脚本，用 `entities_seed.jsonl` 检查候选挖掘逻辑，不作为长期码表。
- `entities_seed.jsonl`：只用于调试和 few-shot 示例，不是永久知识库。

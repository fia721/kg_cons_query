# 公开资料补充与映射说明

## 这次补充了什么

本次没有把公开资料原始文件完整下载到本地，而是先把公开来源作为可审计的 source manifest 接入：

- `data/open_source_manifest.jsonl`
- `data/entity_types.jsonl`
- `data/property_axes.jsonl`
- `data/entities_seed.jsonl`

每个上位类型和属性轴都带 `source_refs`，后续如果要严格复现，可以继续增加下载和解析步骤。

## 来源与用途

| 来源 | 本地 source_id | 用途 |
| --- | --- | --- |
| Schema.org | `schema_org` | 通用实体类型：Organization、Person、Product、SoftwareApplication、CreativeWork、Place、Building、Event、Role |
| Wikidata | `wikidata_core` | instance/subclass、industry、country/location、occupation/employer、part-of/parent organization 等参照 |
| DBpedia Ontology | `dbpedia_ontology` | 组织、人物、地点、建筑、事件等类型体系补充 |
| NAICS | `naics` | 行业轴粗粒度来源，避免按 case 新建行业 |
| ISO 3166 | `iso_3166` | 地区轴参照 |
| NIST RBAC | `nist_rbac` | 角色、权限、权限持有者建模 |

## V3 的类型与属性拆分

上位类型只回答“实体是什么”：

- `organization`
- `organization.company`
- `organization.educational_institution`
- `organization.school`
- `organization.training_institution`
- `organization.financial_institution`
- `organization.bank`
- `person`
- `product.software`
- `content.template`
- `place`
- `building`
- `event`
- `permission`

独立属性轴回答“实体具有什么可交叉属性”：

- `industry`：行业
- `region`：地区
- `org_form`：组织形态
- `hierarchy_level`：层级
- `actor_role`：参与者角色
- `relation`：实体间关系
- `status`：状态
- `capability`：能力/动作
- `format`：格式
- `usage_purpose`：用途
- `time_scope`：时间范围
- `version`：版本

## 当前人工检查结论

已经能产生的有效边界：

- `target_entity=豌豆荚` + `actor_role=employee/author/executive`
- `industry=finance` + `hierarchy_level=branch/independent`
- `industry=finance` + `org_form=bank/branch`
- `org_form=company` + `industry=automotive/software_saas/consumer_retail`
- `capability=permission_management` + `actor_role=tenant_founder/admin`

当前还不够好的边界：

- `region=china` 作为锚点太宽，后续 query 构造时应降低优先级。
- 教育类缺少真实“教育企业”实体，因此不能稳定构造“教育类企业有哪些”；应从训练数据或检索材料中补实体后再生成。
- `status=supported` 作为锚点也偏宽，最好和 `format/capability/type` 共同作为复合锚点。

## 下一步建议

1. 增加 `step2_link_training_entities.py`：用 LLM 或规则把训练材料中的实体链接到 V3 type/property schema。
2. 增加 `step3_select_query_boundary.py`：只选择满足以下条件的候选：
   - positive 和 negative 都在同一召回材料中出现；
   - 至少共享一个中等强度锚点，例如行业、目标实体、共同上位类型；
   - 目标属性不是多属性可共存关系，或 rubric 明确“按材料栏目归属判断”。
3. 增加 source downloader/parser，把公开来源从 `referenced_not_downloaded` 升级为本地可审计表。

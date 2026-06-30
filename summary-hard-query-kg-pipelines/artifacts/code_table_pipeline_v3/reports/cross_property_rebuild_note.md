# V3 重构说明：从属性中心改为实体中心

## 为什么要重构

旧码表的问题是属性轴过细，很多轴其实是某个上位类型内部的分类。这样会导致：

- 新 case 来了就新增属性轴；
- query 容易变成“某细分 label 下有哪些实体”；
- positive/negative 被误认为互斥，但现实中很多属性可共存。

## 新结构

V3 把 KG 分成三层：

1. 实体上位类型：回答“它是什么东西”，例如组织、人物、软件产品、模板、建筑、权限。
2. 独立属性轴：回答“它具有什么可交叉属性”，例如行业、地区、角色、关系、层级、状态、格式、能力。
3. 实体断言：回答“某个实体同时满足哪些类型和属性”。

## query 边界的来源

理想 query 来自：

- shared anchor：positive 和 negative 共享的自然锚点。
- target boundary：positive 具备、negative 不具备的目标属性。

例子：

- `industry=education` 是共享锚点，`org_form=company` 是目标属性，可以构造“教育类企业有哪些？”
- `target_entity=豌豆荚` 是共享锚点，`actor_role=employee` 是目标属性，可以构造“豌豆荚的工作人员有哪些？”
- `industry=finance` 是共享锚点，`hierarchy_level=branch` 和 `org_form=bank` 可构成“地方银行”和“支行/网点”的边界。

## 开源资料如何进入

当前版本把公开来源以 source manifest 的形式接入，并让 type/property 显式记录 `source_refs`。

已接入来源：

- Schema.org：通用实体类型和关系骨架。
- Wikidata：instance/subclass、industry、region、occupation/employer、part-of/parent organization 等属性参照。
- DBpedia Ontology：组织、人物、地点、建筑等类型参照。
- NAICS：行业轴粗粒度参照。
- ISO 3166：地区轴参照。
- NIST RBAC：角色/权限模型参照。

当前还没有把这些公开文件完整下载到本地做自动解析；下一步可以新增 `step0_download_open_sources.py` 和 `step0_parse_open_sources.py`，把公开分类转成可审计的本地表。

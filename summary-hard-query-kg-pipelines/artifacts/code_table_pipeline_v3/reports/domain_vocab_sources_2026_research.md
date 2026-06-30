# 专用领域词表 / 在线词表调研

## 结论

推荐采用“两层 KG”：

1. **本地稳定骨架**
   - 下载并解析权威、稳定、领域粒度一致的词表。
   - 用来定义属性轴、上位词、互斥类别和基础关系。
   - 不依赖 LLM 自造词。

2. **在线长尾补全**
   - LLM 只负责从材料中抽实体短语。
   - 把实体短语发给在线词表服务做 grounding / sibling / parent lookup。
   - grounding 结果再进入 S-Path-RAG verifier，而不是直接作为 query 边界。

工程上第二种方法更好，但不能完全替代本地骨架：在线服务负责覆盖长尾，本地骨架负责约束“往哪里游走”和“哪些属性轴互斥”。

## 2026 年后资料对方案的启发

### OntoKG, 2026

- 链接：https://arxiv.org/abs/2604.02618
- 关键信息：
  - 2026 年工作，强调 ontology-oriented KG construction。
  - 核心是把 property 分成 intrinsic / relational 并路由到 schema module。
  - 目标是让 schema 可复用、可迁移、可用于 entity disambiguation、domain customization、LLM-guided extraction。
- 对当前 pipeline 的启发：
  - 不要让 LLM 自由生成 domain KG。
  - 应把“地区、行业、组织形态、层级、额度类型、履约设施功能”等做成 schema module。
  - 在线词表返回的节点必须被路由到某个属性轴或关系模块。

### S-Path-RAG, 2026

- 链接：https://arxiv.org/abs/2603.23512
- 关键信息：
  - bounded-length candidate paths
  - semantic weighted k-shortest / beam / constrained random walk
  - path scorer + lightweight verifier
  - interpretable path-level trace
- 对当前 pipeline 的启发：
  - 在线服务只生成候选路径。
  - 是否使用该路径构造 query，必须经过 verifier。

### gUFO, 2026

- 链接：https://arxiv.org/abs/2603.20948
- 关键信息：
  - 轻量 foundational ontology。
  - 支持 type typology、intrinsic/relational aspects、situations。
- 对当前 pipeline 的启发：
  - “贷款额度”这类不是实体类型，而更像 intrinsic property / quality。
  - “员工、记者、作者”是 relational role，必须带 target entity。
  - “海外发货仓”可能同时有 facility type、region、fulfillment function，不能压成单一 type。

## 路线 1：可下载到本地的专用词表

### 金融

#### FIBO

- 来源：
  - https://spec.edmcouncil.org/fibo/
  - https://github.com/edmcouncil/fibo
- 形式：
  - OWL/RDF，本地可下载。
  - GitHub 开源，MIT license。
- 覆盖：
  - 金融合同、贷款、证券、商业实体、业务过程、参考数据等。
- 适合补：
  - 贷款、授信、抵押、借款人、贷款合同、额度/承诺类概念。
- 局限：
  - 偏正式金融 ontology，中文业务材料里的“贷款额度 / 可用额度 / 银行卡额度”需要额外做中文别名映射。

#### ISO 20022

- 来源：
  - https://www.iso20022.org/iso-20022-message-definitions
  - https://www.iso20022.org/catalogue/additional-content-messages/external-code-sets
- 形式：
  - 金融消息定义、业务元素、external code sets。
- 覆盖：
  - 支付、证券、卡、账户、交易、金融机构消息。
- 适合补：
  - 账户、卡、支付、信用/借记、交易状态等标准术语。
- 局限：
  - 更偏消息字段和代码集，不是完整业务概念层级。

#### XBRL / IFRS / US-GAAP taxonomy

- 来源：
  - XBRL taxonomy 体系。
- 覆盖：
  - 财务报表概念和层级。
- 适合补：
  - 报表、财务指标、会计科目。
- 局限：
  - 对“贷款额度”这类银行产品属性不如 FIBO/ISO 20022 直接。

### 电商 / 商品 / 采购 / 物流

#### UNSPSC

- 来源：
  - UNSPSC 商品和服务分类。
- 形式：
  - 可下载 code set，通常 PDF 免费，Excel 可能受会员限制。
- 覆盖：
  - 商品、服务、采购分类，含运输、仓储、金融服务、教育培训服务等大类。
- 适合补：
  - 商品/服务分类、采购类 query。
- 局限：
  - 不是实体关系图，更多是 taxonomy。

#### CPV

- 来源：
  - EU Common Procurement Vocabulary。
- 形式：
  - 可下载 code list，存在 RDF/SKOS 版本。
- 覆盖：
  - 公共采购商品/服务，包括运输、仓储、教育、金融服务。
- 适合补：
  - 服务类别、设施/物流服务上位词。
- 局限：
  - 欧盟采购语境，业务口径需映射。

#### GS1 GPC

- 来源：
  - GS1 Global Product Classification / GPC browser。
- 覆盖：
  - 商品分类。
- 适合补：
  - 零售/商品类 query。
- 局限：
  - 访问可能有 403 / 注册 / 授权限制，不适合作为唯一依赖。

#### UN/LOCODE

- 来源：
  - UNECE UN/LOCODE。
- 覆盖：
  - 贸易和运输地点；location function 包括港口、铁路、公路、机场、邮政、内陆清关站等。
- 适合补：
  - “海外仓/本地仓/发货仓”的地区和运输节点维度。
- 局限：
  - 它是地点码，不直接告诉你“海外发货仓”的设施类型。

#### OpenStreetMap taginfo / OSM tags

- 来源：
  - https://taginfo.openstreetmap.org/taginfo/apidoc
- 形式：
  - 在线 API，可查 key、value、similar、combination、wiki pages。
- 覆盖：
  - 仓库、配送中心、物流设施、商店、银行网点、学校等现实地点标签。
- 适合补：
  - `warehouse`、`industrial=warehouse`、`amenity=school`、`amenity=bank`、`office=*` 等设施/地点类概念。
- 优势：
  - API 文档显示数据更新到 2026-06-29。
  - 有 `/api/4/search/by_keyword`、`/api/4/key/values`、`/api/4/key/similar`、`/api/4/key/combinations`。
- 局限：
  - 偏地理实体和设施标签，不覆盖抽象金融属性。

### 科研 / 教育 / 机构

#### OpenAlex

- 来源：
  - https://developers.openalex.org/
- 形式：
  - REST API + full snapshot。
  - 2026 文档显示 API 需要免费 API key；snapshot 免费，季度更新。
- 覆盖：
  - works、authors、sources、institutions、topics、publishers、funders。
- 适合补：
  - 学校、高校、科研机构、学科/主题、论文作者等。
- 局限：
  - 对 K12 学校、培训机构覆盖不如 Wikidata/OSM/本地码表。

#### ROR

- 来源：
  - Research Organization Registry。
- 覆盖：
  - 研究机构、大学、医院、基金机构等。
- 适合补：
  - 学校/科研机构主体识别。
- 局限：
  - 不覆盖普通公司和培训机构。

### 软件 / 安全 / 产品

#### SPDX

- 覆盖：
  - 软件许可证、包、供应链元数据。
- 适合补：
  - 软件包、license、SBOM、开源合规。

#### NVD CPE / CWE / CAPEC

- 覆盖：
  - 软件产品标识、漏洞类别、攻击模式。
- 适合补：
  - 软件产品、安全能力、漏洞相关 query。

#### Schema.org

- 来源：
  - https://schema.org/version/latest/schemaorg-current-https.jsonld
- 覆盖：
  - 通用类型，包括 Organization、EducationalOrganization、LocalBusiness、BankOrCreditUnion、SoftwareApplication、Product、Place 等。
- 适合补：
  - 通用实体类型骨架。
- 局限：
  - 领域细分不足，需要和 FIBO/OSM/ISO/CPV 等合并。

## 路线 2：Wikidata 之外的在线词表 / KG 服务

### 第一优先级：QLever

- 来源：
  - https://qlever.dev/
  - API 示例后端：`https://qlever.dev/api/dbpedia`、`https://qlever.dev/api/yago-4`、`https://qlever.dev/api/osm-planet`
- 覆盖：
  - DBpedia、YAGO、OpenStreetMap、Freebase、UniProt、PubChem、DBLP、OpenCitations、IMDb 等。
- 优点：
  - 一个统一 SPARQL API，可切换多个 KG。
  - 官方页面列出 backend URL。
  - 支持 full-text search 结合 SPARQL。
  - 可作为 Wikidata Query Service 的替代或补充。
- 适合用法：
  - 对抽取实体做 fallback grounding：
    - Wikidata 不通 -> QLever DBpedia/YAGO。
    - 地理/设施类 -> QLever OSM。
    - 学术/论文/作者 -> QLever DBLP/OpenCitations。
- 风险：
  - 仍然是外网服务；devbox DNS/代理不稳定时也可能失败。

### 第二优先级：DBpedia Lookup

- 来源：
  - https://lookup.dbpedia.org/
  - 文档指向 https://github.com/dbpedia/dbpedia-lookup
- 覆盖：
  - Wikipedia/DBpedia 实体、类型、描述。
- 优点：
  - 比 SPARQL 简单，适合短语 entity linking。
  - 对英文通用实体好用。
- 适合用法：
  - LLM 抽出实体短语后，先 lookup 得到 DBpedia resource，再查 type / broader / categories。
- 风险：
  - 中文短语覆盖一般，需要翻译/别名。

### 第三优先级：OpenStreetMap taginfo API

- 来源：
  - https://taginfo.openstreetmap.org/taginfo/apidoc
- 覆盖：
  - OSM tag key/value、相似 key、组合 key、wiki page、使用频率。
- 优点：
  - 对“仓库、学校、银行网点、门店、配送设施”这类实体非常直接。
  - 可查询 tag similarity 和 key-value combinations，适合构造属性轴。
- 适合用法：
  - `海外发货仓` -> 抽取/翻译为 `warehouse` / `fulfillment` / `shipping`。
  - 查 `warehouse` 相关 key/value。
  - 得到 `building=warehouse`、`industrial=warehouse`、`landuse=industrial`、物流设施相关 tag。
- 风险：
  - OSM 是地理标签体系，不适合抽象业务属性。

### 第四优先级：OpenAlex API

- 来源：
  - https://developers.openalex.org/
- 覆盖：
  - 学术主题、机构、作者、作品。
- 优点：
  - REST API 和 snapshot 都可用。
  - 文档明确列出 topics、institutions、authors、works 等实体。
- 适合用法：
  - 教育/科研机构、论文作者、学科主题。
- 风险：
  - 2026 文档显示 API 需要免费 key；不如 DBpedia/QLever/OSM taginfo 即插即用。

### 第五优先级：OLS4 / Ontology Lookup Service

- 来源：
  - EMBL-EBI OLS4。
  - 2025 OLS4 论文说明其支持 OWL2、国际化和兼容 OLS3 API。
- 覆盖：
  - 生物医学、化学 ontology 最强。
- 优点：
  - ontology search / term lookup / broader-narrower 非常成熟。
- 适合用法：
  - 如果训练数据里有医疗、生物、化学、药品领域，优先接。
- 风险：
  - 对当前金融/电商/教育帮助有限。

### BabelNet

- 覆盖：
  - 多语言同义词、词义、百科实体、语义关系。
- 优点：
  - 中文/英文跨语言 lexical grounding 很强。
- 风险：
  - API key 和许可限制；不适合作为批量主链路。

## 推荐的在线 grounding 路由

输入：LLM 从材料抽出的实体短语。

1. 先做轻量领域分类：
   - finance
   - ecommerce_logistics
   - education_org
   - software_security
   - place_facility
   - scholarly
   - generic

2. 按领域路由：
   - finance：
     - 本地 FIBO / ISO 20022
     - DBpedia Lookup / QLever DBpedia
   - ecommerce_logistics：
     - OSM taginfo
     - QLever OSM
     - CPV / UNSPSC 本地 taxonomy
   - education_org：
     - OpenAlex institutions/topics
     - ROR
     - OSM taginfo
     - DBpedia Lookup
   - software_security：
     - Schema.org
     - SPDX / NVD CPE / CWE
     - DBpedia Lookup
   - generic：
     - DBpedia Lookup
     - QLever DBpedia/YAGO

3. 统一输出 grounding schema：

```json
{
  "surface": "海外发货仓",
  "domain": "ecommerce_logistics",
  "source": "osmtaginfo",
  "candidate_id": "tag:building=warehouse",
  "label": "warehouse",
  "parents": ["building", "industrial facility"],
  "properties": [
    {"property_id": "facility_function", "value_id": "storage"},
    {"property_id": "fulfillment_function", "value_id": "shipping"},
    {"property_id": "region", "value_id": "overseas"}
  ],
  "confidence": 0.73,
  "evidence": "taginfo/wiki description or local source sentence"
}
```

4. 进入 S-Path-RAG verifier：
   - 只接受 `same_domain + different_property_value`。
   - 拒绝泛化 anchor，例如 `building`、`object`、`place`，除非 query 明确问设施/地点。

## 针对当前两个失败词的建议

### 贷款额度

本地补：

- FIBO：loan、credit facility、commitment、borrower、lender、loan contract。
- ISO 20022：account、card、credit/debit、limit、balance、available amount 类 message element。

在线补：

- DBpedia Lookup/QLever DBpedia 查 `credit limit`、`loan`、`line of credit`。
- 必要时 BabelNet 做中文 “额度” -> `limit / credit limit / quota / amount` 的跨语言对齐。

属性轴：

- `financial_product_type`: loan / credit_card / account / facility
- `amount_property`: limit / balance / available_amount / quota
- `status`: available / used / frozen / approved

### 海外发货仓

本地补：

- CPV/UNSPSC：warehousing service、transportation/storage services。
- UN/LOCODE：地区/贸易运输节点。

在线补：

- OSM taginfo：warehouse / building=warehouse / industrial=warehouse / logistics facility。
- QLever OSM：查设施实例和 tag。
- DBpedia Lookup：warehouse / fulfillment center / distribution center。

属性轴：

- `facility_type`: warehouse / fulfillment_center / distribution_center / store
- `facility_function`: storage / shipping / fulfillment / customs_clearance
- `region`: overseas / domestic / us / southeast_asia
- `ownership_or_mode`: self_operated / third_party / platform_managed

## 工程优先级

1. 先接在线服务：
   - QLever DBpedia/YAGO/OSM
   - DBpedia Lookup
   - OSM taginfo
2. 同时补本地骨架：
   - FIBO
   - ISO 20022 external code sets
   - CPV/UNSPSC
   - UN/LOCODE
   - Schema.org
3. 把在线返回和本地骨架统一成同一个 grounding schema。
4. 接入 S-Path-RAG verifier。
5. 再让 query synthesis 使用 verifier 通过的路径。


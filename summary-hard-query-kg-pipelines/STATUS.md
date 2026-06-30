# 当前状态

更新时间：2026-06-30

## 目标

整理 summary hard query / KG boundary 两条 pipeline，使后续可以在干净 branch/worktree 中继续开发，并能把关键思路写成文档。

## 已完成

- 12 条 bad case 已抽取并做过原因分析。
- Pipeline1 已形成可复用流程：
  - 抽训练材料。
  - 合成 query/rubric。
  - LLM 语义相似度过滤。
  - 构造测评集。
  - 生成 HTML / retrieval alignment 报告。
- Pipeline2 V3 已形成可复用流程：
  - 构建 type/property/value 码表。
  - 构建 open-KB-backed domain overlay。
  - 加入 Wikidata grounding 和一跳 walk 缓存。
  - 加入 S-Path-RAG 风格路径筛选组件。
  - 加入 domain vocabulary 下载/grounding router 框架。
- 已发现并修复：
  - 在线 KG 脚本 opener bug。
  - S-Path source 无真实命中时的假路径问题。
  - HTML 报告中 raw negative 提及和断言式 negative 命中的区别。

## 当前限制

- devbox 当前外网 DNS 不稳定：
  - 官方词表下载 34 个 URL 全部失败，错误为 `Temporary failure in name resolution`。
  - QLever、DBpedia Lookup、OSM taginfo、OpenAlex/ROR、OLS4 当前也不可达。
- 因此 domain vocabulary 目前只有 bootstrap terms 能跑通逻辑，尚未完成全量官方源下载。
- 当前目录不是有效 git 仓库：
  - `.git` 是空的只读目录。
  - `git status` 返回 `not a git repository`。
- 当前 devbox 无法创建 code.byted 远端仓库：
  - `code.byted.org` DNS 解析失败。
  - 未检测到内部仓库创建 CLI。

## 下一步

1. 等 DNS/代理恢复后重跑：

```bash
python artifacts/code_table_pipeline_v3/scripts/step0_download_domain_vocab_sources.py \
  --timeout 30 \
  --retries 1 \
  --ignore-proxy
```

2. 将 domain vocabulary grounding router 接入 Pipeline2 V3 的 step3 前置候选路径筛选。

3. 给 Pipeline1 和 Pipeline2 分别开独立 branch/worktree，避免继续在同一个工作区里混改。

4. 创建远端仓库后，只提交代码、文档、小型配置和小型样例；大数据、缓存、模型输出通过 manifest 追踪。

推荐先导出干净 snapshot：

```bash
bash scripts/prepare_repo_snapshot.sh /tmp/summary-hard-query-kg-pipelines
```

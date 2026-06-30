# Summary Hard Query / KG Boundary Pipelines

这个工作区用于研究 LLM agentic search 的 summary 任务：从错误 case 和训练召回材料中识别“容易 summary 混淆的边界”，合成 hard query、rubric 和测评集，用来提升 GRPO 训练时的答案约束遵循和低幻觉能力。

## 当前主线

现在有两条相互独立但可以互补的 pipeline：

1. **Pipeline1：材料内主题/属性边界合成**
   - 位置：`artifacts/kg_query_pipeline/`
   - 思路：只看训练数据中的召回材料，由 LLM 抽一级主题、二级属性、positive/negative 边界，合成 query/rubric。
   - 适合：材料本身已经包含足够清晰的正负边界，例如教育类企业 vs 学校、地方银行主体 vs 支行。

2. **Pipeline2 V3：码表/KG 边界游走合成**
   - 位置：`artifacts/code_table_pipeline_v3/`
   - 思路：构建稳定 type/property/value 码表和领域 overlay，通过属性轴、在线/本地词表、S-Path-RAG 风格路径筛选找到相邻边界，再用于 query/rubric 合成。
   - 适合：材料中的实体需要借助专用领域 KG 才能看出细分属性，例如贷款额度、海外发货仓、教育组织形态、银行层级。

## 快速入口

- 总状态：[`STATUS.md`](STATUS.md)
- 总日志：`summary_task_total_log.md`。该文件可能包含历史调试 key，不建议提交远端。
- 工作区规范：[`docs/workspace_playbook.md`](docs/workspace_playbook.md)
- Pipeline 总览：[`docs/pipeline_overview.md`](docs/pipeline_overview.md)
- Pipeline1 文档：[`docs/pipeline1_material_boundary.md`](docs/pipeline1_material_boundary.md)
- Pipeline2 V3 文档：[`docs/pipeline2_code_table_kg.md`](docs/pipeline2_code_table_kg.md)
- 远端仓库创建说明：[`docs/remote_repo_setup.md`](docs/remote_repo_setup.md)
- 线程交接模板：[`docs/thread_handoff_template.md`](docs/thread_handoff_template.md)

## 推荐工作方式

参考当前项目实践，采用：

- `main`：只读主线和样板间，不直接堆实验改动。
- `branch`：一个任务一条分支。
- `worktree`：一个子项目一个独立工作区。
- `thread`：聊天上下文可以换，但状态必须落到 `STATUS.md` 和总日志。

当前目录还不是一个有效 git 仓库；根目录里的 `.git` 是空的只读目录。真正建仓库前建议先按 `docs/remote_repo_setup.md` 初始化一个干净仓库，并避免把 `data/`、大体量 `outputs/`、缓存和日志直接提交。

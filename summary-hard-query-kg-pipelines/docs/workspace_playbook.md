# 工作区协作规范

这份规范对应“main / branch / worktree / thread”的使用方式，目的是防止多个实验互相污染。

## 四个角色

- `main`：主线样板间，只做阅读、查 diff、同步远端。
- `branch`：任务分支，一个任务一条线。
- `worktree`：隔离工位，一个子项目一个目录。
- `thread`：聊天上下文，长了就换；换 thread 前把状态写到 `STATUS.md` 和总日志。

## 推荐方式

1. 主仓库 `main` 保持干净。
2. Pipeline1 和 Pipeline2 V3 分别开独立 branch/worktree。
3. 实验输出只落在各自 `outputs/run_xxx/`，不要跨 pipeline 写文件。
4. 每次重要变更后更新：
   - `STATUS.md`
   - `summary_task_total_log.md`
   - 对应 pipeline 的 README 或 reports。

## 当前建议的 worktree

```bash
# Pipeline1
git worktree add ../kg_build_data_pipeline1 -b feat/pipeline1-material-boundary main

# Pipeline2 V3
git worktree add ../kg_build_data_pipeline2_v3 -b feat/pipeline2-code-table-kg main
```

## Thread 交接

新 thread 开始前，复制 `docs/thread_handoff_template.md`，填入：

- 当前目标。
- 最近改了哪些文件。
- 当前可运行命令。
- 卡点和不要做的事。


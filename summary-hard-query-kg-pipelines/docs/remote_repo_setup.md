# 远端仓库创建说明

目标远端位置：

`https://code.byted.org/users/yuantongfei`

## 当前状态

当前目录不是有效 git 仓库：

- `.git` 是空的只读目录。
- `git status` 返回 `fatal: not a git repository`。
- 当前 devbox 无法解析 `code.byted.org`：
  - `curl -I https://code.byted.org/users/yuantongfei` 返回 `Could not resolve host: code.byted.org`。
- 当前 devbox 没有检测到可用的内部创建仓库 CLI，例如 `code`、`tea`、`gh`、`glab`。

因此不能直接 `git remote add && push`。需要先初始化一个干净仓库，或者复制代码到新的 worktree/repo 目录。

## 建议仓库名

`summary-hard-query-kg-pipelines`

建议远端 URL：

`https://code.byted.org/users/yuantongfei/summary-hard-query-kg-pipelines.git`

## 本地初始化命令

方式一：在当前目录初始化。只有确认空 `.git` 可以删除时使用。

```bash
cd /mlx_devbox/users/yuantongfei/playground/kg_build_data

# 当前 .git 是空目录；如果确认不需要保留，可以删除后初始化。
# rm -rf .git

git init
git checkout -b main
git add README.md STATUS.md .gitignore docs \
  artifacts/kg_query_pipeline/README.md \
  artifacts/kg_query_pipeline/scripts \
  artifacts/code_table_pipeline_v3/README.md \
  artifacts/code_table_pipeline_v3/data \
  artifacts/code_table_pipeline_v3/reports \
  artifacts/code_table_pipeline_v3/scripts \
  artifacts/reports \
  artifacts/scripts

git commit -m "init summary hard query kg pipelines"
git remote add origin https://code.byted.org/users/yuantongfei/summary-hard-query-kg-pipelines.git
git push -u origin main
```

方式二：导出干净 snapshot，再初始化。更推荐。

```bash
cd /mlx_devbox/users/yuantongfei/playground/kg_build_data
bash scripts/prepare_repo_snapshot.sh /tmp/summary-hard-query-kg-pipelines

cd /tmp/summary-hard-query-kg-pipelines
git init
git checkout -b main
git add .
git commit -m "init summary hard query kg pipelines"
git remote add origin https://code.byted.org/users/yuantongfei/summary-hard-query-kg-pipelines.git
git push -u origin main
```

## 注意

- 不要提交 `data/` 和大体量 `outputs/`。
- 不要提交 `qa_agentic/`，里面的历史参考代码包含明文 token/API key。
- 不要提交 `summary_task_total_log.md` 和 `llm_config.jsonl`，总日志和模型配置里可能包含历史 key。
- 需要共享配置时使用 `llm_config.example.jsonl`，真实 key 走环境变量。
- 远端仓库需要在 code.byted 页面或内部 CLI 里先创建。当前 devbox 没有检测到可用的创建仓库 CLI，且 DNS 无法解析 code.byted.org。
- 如果 code.byted 支持网页创建，先在页面上新建空仓库，再执行上面的 `git remote add` 和 `git push`。

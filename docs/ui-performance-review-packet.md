# 界面性能修复独立审查包

这是无 `.ai_state` 项目的普通独立审查输入；`athena review prepare` 已实际执行并返回 no .ai_state。不是 Athena 生成的状态门禁包。

## 范围

主工作区 `/root/myvibe/URnetwork-Proxy`。只读审查，不修改文件、不创建提交、不重启现有服务，不访问用户配置/凭据内容。

本任务：消除管理界面重复渲染、搜索和后台轮询开销。单模块 Bugfix；不做后端结构重构。

本次修改：
- `web/app.js`：缓存格式器、按键复用节点、遥测局部更新、视图懒渲染、搜索按帧调度、后台轮询可见性检查。
- `web/index.html`：界面资源版本从 20261101.1 更新为 20261101.2。
- `tests/browser_addresses.py`：匹配新版资源断言。
- `tests/browser_performance.py`：新浏览器性能与交互回归。
- `docs/ui-performance-*`：本任务设计、实测与审查资料。

仓库在任务开始时已有大量未提交的 HTTPS/代理修复。不要把整个 `git diff` 当作本次变更：只读差异 `/tmp/urnetwork-ui-baseline.XTcq9P/task.diff`，原始前三个文件副本在同目录（`app.js`、`index.html`、`browser_addresses.py`）。该临时目录是本机审查素材；最终证据存于项目 docs。

## 验收

- AC1: 100 个网关只改一个，其余 99 张保持身份/焦点；批量遥测也保持卡片身份；配置改变时操作/复制使用新数据；增删重排正确。
- AC2: 空事件增量不重建日志或选项；新增/改名/删除/筛选正确；隐藏事件/地区视图不提前生成长列表。
- AC3: 隐藏页不发周期请求；恢复立即取网关/事件，到期账户/地区/健康同步；去重和注销清理保持。
- AC4: 国家/网关搜索排序与同帧输入合并正确；编辑国家选择、配置草稿、复制地址不丢失；空和错误恢复正确。
- AC5: 同环境单网关渲染中位数改善至少 50%；Python 全量及原有浏览器测试通过；静态检查与独立审查通过。

## 证据

- `docs/ui-performance-verification.md` 和 `docs/ui-performance-metrics.json`：原始测量、命令、限定、结果。
- 性能测试最终通过，日志 `/tmp/urnetwork-ui-baseline.XTcq9P/performance.log`。
- Python 3.13、3.10 均 148 项通过；三个原有浏览器套件上一轮均通过；最终源码重跑日志 `/tmp/urnetwork-ui-baseline.XTcq9P/browser-regressions.log`。
- `node --check`、`compileall`、`git diff --check` 已通过。
- 8777 服务静态 HTML/JS SHA-256 与工作区一致；没有重启或修改用户配置。

## 审查要求

按 AC 覆盖、正确性、请求/渲染竞态、缓存失效、交互回归、过度设计检查。可重跑纯本地性能回归，但不要调用真实代理或读取 `.local`、`config.json` 内容。性能脚本自动销毁临时服务。

原样返回发现项及恰好一行 `VERDICT: PASS|CONCERNS|REWORK|FAIL`；有 P0/P1 不得 PASS。标注具体文件行号、可复现输入；没有完整审完不得 PASS。不写审查结果文件，由主线程保存。

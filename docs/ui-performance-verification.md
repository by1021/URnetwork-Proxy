# 界面性能修复验证

日期：2026-11-01。界面版本：`20261101.2`。无需后端重启，刷新页面加载新版脚本。

## 实测结果

Chromium 120.0.6099.224；100 个网关、300 条事件、202 个地区。相同离线数据及视口，每项预热 2 次，记录 7 次渲染 + 同步布局耗时的中位数。

| 场景 | 修复前 | 修复后 | 降低 |
|---|---:|---:|---:|
| 单网关活动连接更新 | 369.4 ms | 2.6 ms | 99.3% |
| 全部 100 网关活动连接更新 | 331.8 ms | 15.3 ms | 95.4% |
| 300 条日志无变化重复渲染 | 154.2 ms | 0.8 ms | 99.5% |
| 国家列表无变化重复渲染 | 141.1 ms | 0.2 ms | 99.9% |
| 更新单网关后的原卡片保留数 | 0 / 100 | 100 / 100 | 避免整表替换 |
| 首屏隐藏日志 / 地区卡片 | 300 / 202 | 0 / 0 | 打开视图时构建 |

原始样本与前后脚本 SHA-256：`ui-performance-metrics.json`。数字是本机样本，不代表远端 API 延迟、代理吞吐量或所有设备的帧率；首屏大量新建卡片和结构变化仍需 DOM 构建。

## 修复内容

- 网关按 ID 复用；连接数、延迟、出口 IP、检查时间、失败数、换线数只更新变化的文本。配置、地址、认证、启停或忙碌状态变化重建相应卡片，避免旧操作闭包。
- 日志及最近动态按事件 ID 复用；没有新事件不重新合并排序；筛选选项未变化时保持节点、选中值。
- 隐藏视图不生成长列表；浏览器标签页隐藏时不发周期请求，回前台同步网关/事件，按数据新鲜度补取账户/地区/健康信息，不中止已提交操作。
- 复用数字/日期格式器、地区名称与排序结果；搜索输入按动画帧合并，只处理最新输入。
- 注销清理渲染缓存、定时器和待执行渲染；保留原有请求去重、超时、会话隔离和配置草稿逻辑。

## 验收证据

| 验收 | 检查 | 结果 |
|---|---|---|
| AC1 局部更新 | `tests.browser_performance`：100 卡片身份、复制按钮焦点、全部遥测更新、改名/端口/认证/国家/启停状态、增删重排 | 通过 |
| AC2 列表复用 | 空增量事件、300 条上限、新事件、改名与历史网关筛选、级别过滤、隐藏视图 | 通过 |
| AC3 生命周期 | 可见性切换与全部周期回调、过期数据恢复、5 个并发刷新合并、注销后零轮询 | 通过 |
| AC4 交互 | 中文搜索、排序、同帧输入合并、编辑国家选择与选项身份、空/失败/成功恢复 | 通过 |
| AC5 性能 | 相同 Chromium、修复前后源码、原始计时样本 | 单项更新改善超过 50% 门槛 |
| 后端回归 | Python 3.13 / Python 3.10，各 `unittest discover -s tests -q` | 各 148 项通过 |
| 原有 UI | `tests.browser_smoke` / `tests.browser_config` / `tests.browser_addresses` | CRUD、会话、配置草稿/冲突、三协议复制、7 种访问入口、移动布局、深浅主题通过；无 JS/CSP 错误 |
| 静态检查 | `node --check web/app.js`、`compileall`、`git diff --check` | 通过 |
| 当前运行服务 | 8777 端口 GET HTML 与版本化 JS，对照工作区 SHA-256 | HTTP 200，内容一致，`Cache-Control: no-store` |
| 配置保护 | 任务开始前后 `config.json` SHA-256 | 一致；无服务重启、真实代理创建/删除或用户配置写入 |

性能脚本通过真实 Chromium 与临时 HTTP 服务运行；仅离线 API 数据被替换，测试内暴露渲染函数用于计时。没有放宽生产 CSP。可见性测试注入 `document.hidden` 并触发周期回调，不宣称测量了操作系统后台节流。

## 复跑

```bash
athena run -- node --check web/app.js
athena run -- python -m unittest discover -s tests -q
athena run --env CHROMIUM_PATH=/usr/bin/chromium -- python -m tests.browser_performance
athena run --env CHROMIUM_PATH=/usr/bin/chromium --env SCREENSHOT_DIR=/tmp/urnetwork-ui-screenshots -- python -m tests.browser_smoke
athena run --env CHROMIUM_PATH=/usr/bin/chromium --env SCREENSHOT_DIR=/tmp/urnetwork-ui-screenshots -- python -m tests.browser_config
athena run --env CHROMIUM_PATH=/usr/bin/chromium --env SCREENSHOT_DIR=/tmp/urnetwork-ui-screenshots -- python -m tests.browser_addresses
```

Playwright 与 Chromium 是可选测试工具，不新增生产依赖。可省略 `CHROMIUM_PATH` 使用 Playwright 已安装的浏览器。修复前对比使用 `--source` 指定保存的旧 `app.js`，加 `--measure-only` 只记录性能、不执行新行为断言；`--output` 可输出 JSON。

## 先红后绿 / 审查

- 原实现：回归失败于“只修改一个网关却替换全部卡片”；同时记录隐藏页面提前生成 300 条事件和 202 张地区卡片。
- 扩展批量遥测场景后，改为字段级更新；原“保留其他 99 张卡片”断言允许所有 100 张都保留，仍捕获原始整表重建故障。
- 空地区恢复且搜索无匹配：新增测试先复现错误提示被缓存；渲染签名增加总地区数后通过。
- 独立只读审查原样结果另存 `ui-performance-review.md`。
- 项目没有 `.ai_state`；`athena run` 执行检查但提示不记录 sprint evidence，`athena review prepare` 返回 no .ai_state。未擅自初始化；本报告与普通独立审查不冒充 Athena 门禁状态 PASS。

## API 依据

- MDN `Number.prototype.toLocaleString` 官方内容源码已核对：重复调用场景复用 `Intl.NumberFormat`。
- MDN `Page Visibility API` 官方内容源码已核对：不可见仪表板暂停服务器轮询。
- 项目实现依据：`web/app.js`；回归依据：`tests/browser_performance.py`。不修改后端协议或缓存语义。

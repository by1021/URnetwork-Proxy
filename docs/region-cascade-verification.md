# 地区联动选择验证

date: 2026-11-08
contract: region-cascade-design.md

## 范围与环境

Python 3.13.5；复用 `/tmp/doproxy-direct-venv/bin/python` 的 Playwright / Chromium 环境。浏览器运行真实管理 HTTP 服务，位置供给与账户使用临时样本。没有修改 `data/config.json`、新建真实上游、清理账户资源、重启现有服务或推送。

## 验收证据

| 验收 | 证据 | 结果 |
|---|---|---|
| AC1 自动加载、无搜索 | `tests/region_cascade_ui_smoke.py` | PASS；未选国家不请求，国家／模式变化自动加载，提供刷新入口 |
| AC2 官方国家名、类型／国家／供给过滤 | 同上 | PASS；使用 United States / Japan，排除城市、跨国地区与零节点，保存原始 ID |
| AC3 国家／模式／表单竞态 | 同上 | PASS；国家搜索导致旧选择消失也清空地区，迟到请求不覆盖新状态 |
| AC4 无供给、加载失败、过期、刷新恢复 | 同上 | PASS；过期地区不用于新绑定，无全国回退 |
| AC5 编辑保留 | 同上 | PASS；缓存刷新失败、目录字段变化、编辑期间国家目录变空及恢复后 ID 变化仍保留既有绑定；选了另一国家后目录变空不会偷偷恢复原国家 |
| AC6 创建与旧行为 | `tests/proxy_pool_ui_smoke.py --integrated`、组件版、`tests/gateway_ui_smoke.py`、`tests/login_clipboard_ui_smoke.py` | PASS；三条主备的地区 wire ID 一致，城市、国家与登录／剪贴板回归通过 |

全量 120 项单测、7 项登录／剪贴板浏览器测试、新地区联动、组件／生产管理器代理池及既有网关浏览器检查通过；JS 语法、Python 测试语法及 diff 空白检查通过。

红测试首先复现地区搜索框仍显示；绿色实现移除地区搜索。回归发现静态资源／界面版本须一致，现已同步为 `20261108.5`，未放宽测试。原始红／绿输出保存在 `.local/region-cascade/`。

独立审查发现编辑期间国家目录变空会清空原地区绑定；新增回归首先复现（`country-refresh-red.log`），修复为在下拉选项保存国家代码，以便官方 ID 与已保存占位 ID 双向恢复。当前地区联动回归通过（`region-ui.log`）。

复核发现同一地区 ID 的新目录缺少父 ID 时会覆盖已保存绑定字段，导致下一次刷新失败后保存无关字段被服务端拒绝（原样结论 `review-rework.txt`）。新增元数据变化回归首先复现（`binding-metadata-red.log`），改为完整保留已保存绑定字段、只更新供给元数据；当前地区联动回归通过。

本轮复跑时并发检查中 2 项既有网关操作单测失败（`unit-concurrent-red.log`）；同一源码独立重跑 120 项全部通过（`unit.log`）。未修改后端源码或放宽断言。

## 审查与部署边界

仓库没有 `.ai_state`，没有擅自初始化。`athena run` 明确提示不记录原生 sprint 证据；`athena review prepare` 退出 2（缺少状态目录）。独立只读审查使用本轮前快照、增量补丁、源码摘要及日志的本地包，不冒充原生 Athena PASS/current。

独立审查：PASS；国家目录刷新与地区绑定字段保留问题均已修复，独立复跑地区联动浏览器回归通过，源码摘要无漂移。原样结论保存在 `.local/region-cascade/review-pass.txt`。此结论为本地独立审查，不代表原生 Athena PASS/current。

当前 Docker 服务仍运行修改前界面 `20261108.4`，更新运行服务需用户确认；已核对 14 个非 UI 运行源码文件与现有容器一致。本轮仅修改代码、测试及文档，未部署或重启。

## 已知限制

- 地区列表是官方当前返回的可用范围，不是所有行政区的永久清单。
- 本轮未新建真实上游验证实际地区出口，原有 location_id 绑定逻辑未改。
- 全量单测保留基线已有的 unclosed event loop ResourceWarning，非失败。

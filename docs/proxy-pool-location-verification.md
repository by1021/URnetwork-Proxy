# 代理池与精确位置验证

date: 2026-11-08
base_commit: e2f3c820e1613036b376ee7b0bb891793a754abb
contract: proxy-pool-location-design.md

## 环境与范围

Linux x86_64、Python 3.13.5；浏览器测试使用 Playwright 1.64.1 / Chromium 156。运行源码依赖仍为 Python 3.10+ 标准库；已检查 3.10 语法，未在 3.10 解释器实跑。

验证使用临时目录、回环 HTTP/SOCKS/TLS 服务及模拟官方控制面；集成浏览器场景使用生产 GatewayManager 和真实管理 HTTP 服务。未访问真实账户、创建付费上游、修改现有 data/config.json、重启已有服务或推送。

仓库没有 `.ai_state`，未擅自初始化。检查通过 `athena run` 执行；CLI 明确提示无 active sprint、不记录门禁证据。`athena review prepare` 同样退出 2（缺少状态目录）；独立只读审查使用绑定输入摘要的本地审查包，不冒充 Athena PASS/current。

## 可复跑检查

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/proxy_pool_runtime_smoke.py
python tests/proxy_pool_ui_smoke.py
python tests/proxy_pool_ui_smoke.py --integrated
python tests/gateway_ui_smoke.py
python tests/login_clipboard_ui_smoke.py
node --check web/app.js
git diff --check
```

浏览器命令的 `python` 须位于安装 Playwright 和 Chromium 的开发环境；本次使用 `/tmp/doproxy-direct-venv/bin/python`，没有更改该既有环境。

| 证据 | 结果 | 覆盖 |
|---|---|---|
| 全量单测 | 120 项通过 | 配置默认值、主备故障、迟到结果、缩容/停用/清理、位置父级、Pro 权益、脱敏错误及账户限速 |
| proxy_pool_runtime_smoke | PASS（主仓及受控快照） | 真实 TCP、HTTP POST、CONNECT、TLS HTTPS 网关、SOCKS5；连续两条上游失效后第三条接管，慢创建不阻塞已有热备，健康隧道保留，提交的 POST 不重放 |
| proxy_pool_ui_smoke --integrated | PASS（主仓及受控快照） | 城市/地区各三节点使用同一 wire location_id；3→2→1 缩容、主用不变、回收节点仍展示；空结果/过期/父级筛选与编辑保留 |
| 独立 UI / 既有管理 / 登录回归 | 两项 smoke 及 7 项登录测试通过 | 默认端口、国家硬约束、字段校验、入口/剪贴板行为 |
| JS 语法 / diff 检查 | PASS | 无语法或补丁空白错误 |

运行日志与受控快照/结果保存在 `.local/proxy-pool-location/`。本地故障切换的毫秒测量只代表立即拒绝连接的回环样本；公网握手超时、官方供给、独立出口数量与恢复时间不作生产保证。

快照显式要求 runtime smoke、集成 UI smoke 及 FixtureRelay 输入；扫描器排除其它含假密码模式的非必需测试文件，未放宽扫描规则。这些单测/旧 UI 回归是在主仓另行运行，不以快照代替全量回归。

## 有界清理检查

| 检查 | 结论 |
|---|---|
| 临时代码 | 改动写集无 debugger、console.log 或遗留 TODO；测试控制面明确为 fixture |
| 注释 | 候选建连预算、提交后不重试、generation 与定向 drain 有解释 |
| 重复 | 独立 UI 快照用于组件测试，生产 manager 集成测试另行验证，未复制生产调度实现 |
| 阻塞与低效 | 远端请求走 to_thread；内存选路不等待创建/探测/清理；目录缓存与连接候选有界 |
| 过度设计 | 保留单上游接口兼容，不引入运行时第三方依赖或第二状态机 |

架构与使用说明已更新为主备池、精确位置、账户容量与会话边界。独立审查包及原始输出放在 `.local/proxy-pool-location/`，审查后的源码修正必须重新验证并生成新输入。

三轮独立审查的七项 P1 均新增红→绿测试并修复：失败后恢复监听/鉴权、旧成功探测保留较新账户退避、满额时有界清理本工具坏资源、退休取消建连中握手并在提交前复核候选、端口/密码编辑撤销迟到旧 bind、停止网关后的迟到 429 仍约束整个账户，以及 HTTP 429 未带结构化错误码时仍遵守服务器 Retry-After。HTTP 503 带有效 Retry-After 同样尊重期限，无该字段时保留一般退避；既有较长期限不缩短，等待值仍限制在 60–3600 秒。另验证 unique-auto 恢复资源不被普通 trim 提前清理，以及有活跃流时 relay.close 有界结束。共新增 17 条回归；原始 REWORK 记录保留为 `review-output-r1.md` / `review-output-r2.md` / `review-output-r3.md`，最终复审针对修正后的输入。

## 会话接力复核

接力源会话为 `1794127358-03712e728f9b`。先核对第三轮审查的 30 文件摘要无漂移，再重跑单测、真实连接和浏览器回归。新增 HTTP 限流修复在原有 core 隔离 worktree 完成后整合；红测试、补丁与正式 ApiError 构造器直验日志均保存到 `.local/proxy-pool-location/`。本轮继续保持无真实账号操作、无现有服务重启、无推送；原生 Athena 状态目录仍未初始化。

## 已知未覆盖

- 真实付费上游、实际账户 Pro/Embed 配额及 unique-exit 恢复时间。
- Python 3.10 解释器、Windows/macOS 与容器实跑。
- 全量单测存在基线已有的 unclosed event loop ResourceWarning，非测试失败。

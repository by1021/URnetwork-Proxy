# 访问地址自适应修复

路径：Bugfix。仓库未初始化 `.ai_state`，本轮直接工作，不初始化状态。保留开始时所有未提交改动及本地配置/网关数据。

## 当前 / 期望 / 不变行为

- 当前：页面主地址来自服务器出站路由，容器、NAT、域名或端口转发下不等于用户实际访问入口；双栈初始化可能静默忽略 IPv6-only 设置失败；当前没有 8777 监听进程。
- 期望：浏览器地址栏为访问入口唯一来源，保留协议、主机与端口，IPv6 格式正确；服务器探测 IP 仅作诊断。监听失败有明确错误，不给出虚假的启动成功信息。
- 不变：管理登录、Host 白名单、同源与 CSRF 校验；默认回环管理监听、显式远程监听配置；网关仍按真实监听地址展示，不把回环代理伪装为远程代理。已有配置、上游账号与网关状态不自动修改。

## 验收

- AC1: 登录页、总览、配置面板主机与当前访问地址同步，覆盖 localhost、IPv4、IPv6、域名、转发端口与 HTTPS；主入口不被服务器路由 IP 或候选 URL 覆盖。
- AC2: 运行信息接口失败仍显示已知访问入口；服务端 IP / 监听信息独立标注。远程页面中回环代理明确标明服务器本机使用。
- AC3: 双栈请求遇到 IPv6 不支持或 IPv6-only 时回退 IPv4；真实 IPv4/IPv6 请求通过，端口冲突给出可操作错误提示。
- AC4: 临时隔离状态实际启动 8777，访问首页、健康、运行信息和登录；Chromium 验证直连/域名/转发入口、地址显示与网关操作；全量回归通过。

## 边界

浏览器地址自适应不创建进程、不修改防火墙或端口映射；代理出口 IP 继续来自健康探测。真实运行验证不调用用户云端资源、不恢复既有网关，临时进程测试后关闭。代理是否需要远程访问已询问，未确认前保持原有范围。

## 验证记录（2026-10-31）

| 范围 | 检查 | 结果 |
| --- | --- | --- |
| 失败复现 | 原前端副本 + `tests.browser_addresses` | 实际页面显示服务端诊断 IP，不存在自适应访问主机，断言失败 |
| 失败复现 | 双栈设置失败 / 内核保留 IPv6-only | 两项测试失败，旧实现吞掉异常且继续绑定 |
| 失败复现 | CLI 端口占用 | 抛出 OSError traceback，未给出预期监听提示 |
| AC1–2 | `athena run -- env CHROMIUM_PATH=/usr/bin/chromium python -m tests.browser_addresses` | 7 场景通过：IPv4、localhost、IPv6、域名、不同转发端口、真实 TLS 终结、诊断接口失败；含登录、配置页、移动端布局、同源 API 及回环网关提示 |
| AC3–4 | `athena run -- python /tmp/urnetwork-verify-8777.py` | 实际 `main.py` 监听 8777；IPv4/IPv6 回环及本机网卡访问首页、health、runtime、登录均 HTTP 200；真实端口冲突提示通过，Ctrl+C 干净退出 |
| 回归 | `athena run -- python -m pytest -q` | 125 passed，67 subtests passed |
| 回归 | `tests.browser_smoke` / `tests.browser_config`（经 athena run） | 登录、会话、网关 CRUD、配置保存/冲突/遮罩与桌面/移动端均通过，无 JS/CSP 错误 |
| 静态 | `node --check web/app.js`、`python -m compileall -q core main.py tests`、`git diff --check` | 通过 |

原始日志：`/tmp/urnetwork-address-{red-unit,red-startup,red-browser,browser,8777,pytest,smoke,config}.log`。截图：`/tmp/urnetwork-address-evidence/adaptive-address.png`。临时测试进程已关闭，8777 不遗留后台监听；`config.json` 与既有 `.local/gateways.json` 的 SHA-256 前后相同。

测试中的域名由 Chromium 本机解析规则指向回环；TLS 转发服务、本地网关客户端及诊断接口故障均为临时测试设施，不使用真实云端代理资源。测试覆盖本机网卡直连，不代表从外部网络穿过防火墙的连通性。

`athena status` / `athena review prepare` 确认没有 `.ai_state`，`athena run` 执行检查但不记录 sprint 凭证；独立审查使用外置材料包，不宣称生成 Athena 门禁状态。

## 独立审查与最终复核

独立 reviewer 审查材料包 `/tmp/urnetwork-address-review-packet.md`，返回 `VERDICT: PASS`，AC1–AC4 无需修复项。审查方另行复跑 43 tests、16 subtests 及 7 个 Chromium 场景，均通过。原样结果：`/tmp/urnetwork-address-review-result.txt`。

最终源码综合复核日志：`/tmp/urnetwork-address-final.log`，包含静态检查、7 个地址场景、两套既有 Chromium 回归及真实 8777 启停；全量 pytest 最后一次为 125 passed、67 subtests passed。代码未再变动，仅补记本段审查结论。

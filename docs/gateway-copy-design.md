# 网关复制地址跟随访问主机

Bugfix；延续访问地址自适应任务。用户明确追加：HTTP/SOCKS 复制地址仍是旧 IP，要求修改。没有 `.ai_state`，直接工作，不初始化状态。

## 当前 / 期望 / 不变

- 当前：网关卡片显示和复制使用 `gateway.host`，仍是监听回环 IP，未跟随浏览器入口。
- 期望：卡片 HTTP / SOCKS5 地址与剪贴板内容统一使用 `location.hostname`，端口仍是对应网关端口；IPv6 保留一对方括号。
- 不变：HTTP 代理即便管理页为 HTTPS 仍使用 `http://`；SOCKS5 使用 `socks5://`；不改变监听范围、用户配置、网关状态及代理出口 IP。访问主机不等于实际监听主机时，卡片独立标注实际监听地址并提示端口转发/映射及连通性未验证，不宣称复制即连通。

## 验收

- AC1: IPv4、IPv6、localhost、域名、不同管理转发端口与 HTTPS 下，两种卡片地址、title、复制值均使用当前访问主机 + 网关端口。
- AC2: 真实 Chromium 剪贴板成功路径与不可用/拒绝时的手动复制弹框均输出相同地址；HTTPS 管理页不改变代理协议。
- AC3: 地址展示与真实监听分开标注；保持已有账号、网关生命周期和配置行为；旧浏览器回归与单测通过。

本次要求覆盖前次「复制保留回环地址」决策；后端回环监听策略保持不变。

## 验证

- 红：Chromium 通过 `localhost` 打开时，期待 `localhost:10801`，旧实现实际为 `127.0.0.1:10801`；日志 `/tmp/urnetwork-gateway-copy-host-red.log`。
- 绿：`athena run -- env CHROMIUM_PATH=/usr/bin/chromium SCREENSHOT_DIR=/tmp/urnetwork-gateway-copy-evidence python -m tests.browser_addresses`，7 个场景通过。覆盖两种代理显示/title、真实系统剪贴板写入读取、拒绝权限和 Clipboard API 缺失时手动复制弹框；同一网关端口不随管理端口或 TLS 协议变化。
- 全量：`athena run -- bash -c 'node --check web/app.js && python -m pytest -q && git diff --check'`，125 passed、67 subtests passed；日志 `/tmp/urnetwork-gateway-copy-tests.log`。
- 本次代码仅修改 `web/app.js` 的地址生成及提示，浏览器回归更新 `tests/browser_addresses.py`；使用说明同步 `docs/gateway-guide.md`。不触碰后端监听、用户配置或已有网关数据。
- 既有 Chromium 回归：`tests.browser_smoke` 与 `tests.browser_config`（经 `athena run`），全部通过，无 JS/CSP 错误；日志 `/tmp/urnetwork-gateway-copy-regression.log`。截图 `/tmp/urnetwork-gateway-copy-evidence/adaptive-address.png` 已人工查看。

当前未初始化 `.ai_state`，`athena run` 执行验证但不记录 sprint 凭证；独立审查使用外置材料包，不宣称生成 Athena CLI 门禁记录。

## 独立审查

材料包 `/tmp/urnetwork-gateway-copy-review-packet.md`；独立 reviewer 返回 `VERDICT: PASS`，AC1–AC3 无需修复项，并另行复跑 7 个 Chromium 场景与静态检查通过。原样输出 `/tmp/urnetwork-gateway-copy-review-result.txt`。源码在审查期间未修改。

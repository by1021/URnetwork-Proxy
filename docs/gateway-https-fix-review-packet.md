# 网关创建 / HTTPS 修复：独立只读审查

项目：/root/myvibe/URnetwork-Proxy。需求：网关创建报“参数无效”，没有 HTTPS 代理；修复并实测可用。
设计与 AC：docs/gateway-https-fix-design.md。
项目没有 .ai_state；`athena review prepare` 返回 no .ai_state found。遵循本项目现有约定，以普通独立审查留档，不伪造 Athena gate PASS。

请只读审查，不编辑文件、不修改 config.json、不读取或打印用户凭据。审查所有本轮源码与新增测试，优先关注 TLS framing / EOF / 并发和认证、Python 3.10 兼容、证书信任及配置接线、参数校验兼容和 API 返回信息。不因无 .ai_state 阻止普通代码审查。

## 工作区边界

任务开始前已有未提交的 README、core/checker.py、client.py、config.py、multiplexer.py、protocol.py、tests/test_protocols.py 和 core/tls_helper.py；已保留其功能方向并修正其中未完成实现。不要把既有远端节点生命周期、全接口默认等决定当作本轮新特性。

本轮核心变更：
- core/gateway.py、web_server.py：共享字段校验、地址归一化、明确字段错误、证书配置传递与独立 TLS 启动错误。
- core/tls_helper.py、multiplexer.py：MemoryBIO 接收已读取的 ClientHello，同端口 TLS，EOF 传播；证书按实例缓存，显式错误不回退到其他服务私钥，默认证书 SAN 与权限。
- core/checker.py、protocol.py：HTTPS 上游及 TLS-in-TLS 探测，不静默降级显式 HTTPS；健康检查证书验证未关闭。
- web/app.js、index.html：HTTPS 复制、凭据 URL 编码、证书配置输入、信任提示、版本 20261101.1。
- config.example.json、README、requirements、docs/gateway-guide.md：证书生成依赖与使用要求。
- tests/test_gateway_api_regressions.py、test_https.py、test_protocols.py、browser_addresses.py、test_gateway.py、live_gateway_verify.py：回归及真实出口测试。

## 证据（原始输出可读）

- /tmp/urproxy-api-red.log：原版合法别名创建 400、重复端口/校验无字段、国家归一化失败。
- /tmp/urproxy-nested-red.log：原版 HTTPS-over-HTTPS 探测 SSLError。
- /tmp/urproxy-tls-connect-eof-red.log：原版 TLS CONNECT 上游 EOF 导致超时。
- /tmp/urproxy-final-tests.log、/tmp/urproxy-final-tests310.log：Python 3.13 / 3.10 全量测试。
- /tmp/urproxy-https-final.log：11 项真实 TLS socket / 证书测试通过。
- /tmp/urproxy-browser-final.log、/tmp/urproxy-addresses-final.log、/tmp/urproxy-config-browser.log：真实 Chromium CRUD、三协议复制、7 种入口、配置和移动布局。
- /tmp/urproxy-live-final.log：真实 URnetwork US 临时网关，HTTP / HTTPS / SOCKS5h 均访问 HTTPS 目标成功，证书校验开启，主动健康检查通过，临时远端资源回收。
- config.json 哈希前后一致；不改用户现有网关。现有服务重载与在线复核由主线程在审查窗口中做，不改源码。

请返回有证据的发现（严重级别、文件行号、复现及建议）。无问题则说明未发现阻断项，最后独立一行 `VERDICT: PASS`；需修复则 `VERDICT: REWORK`。这是代码审查结论，不是 Athena 状态门禁。

## 第二轮复审

首轮原样结论：docs/gateway-https-fix-review-1.md。
- P1 已修：明文 HTTP / SOCKS5 保留原生上游协议，仅本地 HTTPS 请求优先选择 HTTPS 上游。新增 test_plain_protocol_half_close_with_https_upstream_available，HTTP 与 SOCKS5 均断言 write_eof 后收到完整 reply:upload。
- P2 已修：_update 重绑定单独捕获 TLSConfigurationError，保留旧监听、返回 local_tls_failed。新增 test_rebind_bad_certificate_reports_tls_and_preserves_old_listener。
- 两项测试先红后绿，原始失败见 /tmp/urproxy-review-red.log。
- 最新 Python 3.13 / 3.10 全量各 148 项通过，日志路径仍为 /tmp/urproxy-final-tests.log 和 /tmp/urproxy-final-tests310.log。
- 当前源码指纹：/tmp/urproxy-reviewed-source.sha256（core/*.py、web/*、tests/*.py、main.py、requirements.txt、config.example.json）。
- 当前服务已重载最终源码。在线真实浏览器与 curl / IPv6 Python TLS 验证正在执行，输出 /tmp/urproxy-online.log。

请重点复核首轮两项关闭情况及修改影响，仍为只读；返回 findings 与 VERDICT。

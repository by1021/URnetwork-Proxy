# 全面可靠性修复设计

路径 System；req: docs/requirements/reliability.md。无 `.ai_state`，按约定直接工作，不初始化状态。使用隔离 worktree，多写者独立文件责任，主线程整合。

## 根因
ConnectionResetError 发生在 BaseHTTPRequestHandler.handle_one_request 的 request-line/header 读取阶段，早于现有 _handle 的 try/except。应在连接生命周期的外层处理已知断连；不能 blanket 忽略未知异常。TCP peer 可能是浏览器或中间设备，不能据此断言真正故障归属。

## 契约

1. 管理服务：外层捕获 ConnectionResetError/BrokenPipeError/ConnectionAbortedError 等已知传输终止，结构化有限诊断+计数；未知异常仍记录。过载提供有界 HTTP 503；不记录 cookies、请求体、查询参数、密码。`/api/health` 保持原有 liveness 形状，新增认证后的 `/api/diagnostics` 返回连接计数及 readiness（enabled 网关非 running / auth_missing 等清晰分层）。
2. 网关配置保留 `host` 为实际监听地址，新增 `access_mode: local|remote`（默认 local）、`advertise_host: ''`（空取浏览器主机）、`advertise_port: 0`（0 取实际端口）、`allowed_sources: []`（CIDR，remote 用户可显式限制）。remote 强制 username/password；API snapshot 返回 `auth_required`、`username` 但不回传密码。remote 凭据受限权限落盘以支持重启，legacy local 认证仍按原规则缺密码时禁止恢复。host 不接受任意域名，只接受 IP 或 localhost；advertise_host 严格主机语法，无协议路径端口凭据。0.0.0.0 仅 IPv4、:: 尽量双栈，实际能力如实快照。
3. 网关操作返回 snapshot 保持兼容，可附 `operation: {status: applied|failed|pending, message/code}`；修改失败不得附 applied，客户端失败保留草稿，创建配置已存但运行未就绪要分别提示。服务端不把业务失败仅用 HTTP 200 掩盖；优先显式 operation 状态，前端校验。
4. 清理：`URClient.clean_clients` 返回 `ok,status,total,proxies_removed,failed:[{client_id,error}],clients_reset`，失败不泄漏异常原文；可用 `client_ids` 列表限制失败项重试；部分删除失败时不做全账号 reset，避免绕过单项结果。`/api/proxies/clean` GET无效，POST 支持 `scope: account|owned`（默认 account 兼容，UI 明确选择）；account 必须在没有网关且无 owned/inflight 时进行，和其他管理操作互斥；owned 只操作本工具网关并保留失败状态，不越界清理。避免现有 FakeManager 测试因缺方法而静默绕过。
5. Relay：连接来源限制在握手前；只在上游连接/握手故障调用 on_result(False)，正常上游连接可调用 True，客户端断开/鉴权失败不触发误判；回调不是直接换线。出口 UI 标“最近探测出口 IP”。
6. 安全配置：新 `web_secure_cookie: false`；显式 true 时会话设置 Secure，不盲信 Forwarded；非回环绑定使用默认密码时拒绝启动并提示本机 getpass 配置命令。回环默认账号兼容。配置脚本原子写 0600、不输出秘密；TLS 入口以部署配置验证，不擅自修改机器。服务模板固定目录和非 root 用户、优雅 SIGTERM、日志输出，安装脚本需 dry-run 和显式 apply。

## 验收

- AC1: 请求行之前/请求头中途 RST、响应时断连均不出现未捕获 traceback；后续请求 HTTP 200；配额释放，未知程序错误仍可见。
- AC2: 未登录 health 兼容，diagnostics 需登录，日志脱敏；过载可收到 503，拒绝计数可观测。
- AC3: remote 模式 HTTP/SOCKS5 真实非回环访问成功、错误/缺失密码被拒、CIDR 限制生效、重启保留认证；旧本机网关兼容，不偷偷公网开放。
- AC4: advertised host/port 生成可复制 URL，IPv6 正确；页面可创建/编辑远程认证与来源限制，密码不回显，实际监听与广告地址分开。
- AC5: 端口占用/上游候选失败不显示已保存，草稿保留；恢复条件后成功；启动失败与配置落盘分别可见。
- AC6: 部分清理返回失败明细、重试限定失败项，活跃网关阻止全账号清空，本工具回收不影响其他资源，清理/网关/账号操作互斥。
- AC7: 上游失败接通提前健康检测；客户端错误不触发；周期自愈、资源回收、事务回滚回归通过。
- AC8: 非回环默认密码防护、Secure cookie 开关、秘密配置权限与低权限 HTTPS/service 部署路径可验证；SIGTERM 干净关闭；不改变真实用户配置与服务。
- AC9: 单测、Chromium 核心流程、临时 socket/跨网络命名空间集成通过；架构与部署说明更新，独立审查通过。外部手机测试和真实上线结果单独标记。

## 切片 / ownership
A transport: core/web_server.py，tests/test_web.py，tests/test_runtime_address.py，新 transport 测试。
B gateway: core/gateway.py，core/multiplexer.py，tests/test_gateway.py，tests/test_protocols.py，tests/test_integration.py，新 remote 测试。
C UI: web/*，tests/browser*.py。
D security/client/deploy: core/config.py，core/client.py，main.py，config.example.json，tests/test_client.py，tests/test_config_editor.py，systemd/*，scripts/*，新增安全测试。
主线程：整合、运行验证、docs、剩余交叉合同测试；不覆盖其他写者。

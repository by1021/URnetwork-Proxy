# 当前架构

| 层 | 文件 | 职责 |
|---|---|---|
| 官方接口 | `core/client.py` | 认证、GET 401 有界重登、位置绑定创建、指定 client_id 回收；创建 POST 不盲重放 |
| 国家目录 | `core/locator.py` | provider-locations 全量国家、原始 provider_count、刷新时间及过期状态 |
| 数据面 | `core/multiplexer.py`、`core/protocol.py` | asyncio 单 TCP 端口识别 HTTP / SOCKS5；鉴权、完整握手、背压、半关闭 |
| 健康检查 | `core/checker.py` | HTTP CONNECT / SOCKS5 CONNECT → TLS 校验证书 → 出口 IP；两个探针交叉确认 |
| 生命周期 | `core/gateway.py` | 配置、每网关操作锁、候选验证、原子换线、所有权登记、延迟清理、恢复和退避 |
| 本地管理 | `core/web_server.py` | 事件循环桥接、回环 REST API、管理会话、CSRF、凭据更新、静态资源 |
| 持久化 | `core/storage.py` | 原子替换、单进程 OS 文件锁；覆盖凭据与用户 config.json 分离 |
| 前端 | `web/` | 无 CDN 中文管理页；轮询、搜索、CRUD、地址复制、日志 |
| 启动 | `main.py`、`start_web.bat` | 保留原 CLI；新增 Web 模式 |

## 数据流与不变量

客户端 → 固定本地端口 → 协议处理器 → 当前远端代理 → 目标。
国家绑定作用于创建参数 `connect_location_id.location_id`，不只是显示国家名。
接入时读取当前上游，建立后的 TCP 流固定使用该上游。候选未通过完整检查时保留旧上游与旧绑定。

管理器使用单个 asyncio 循环；阻塞的官方请求在线程执行。URClient 的认证和请求由 RLock 保护；更新凭据时禁止并发网关操作。
管理 API 对同一网关的 PUT 串行执行读取、合并、国家校验和提交；不同网关独立。高级本地代理密码仅驻留内存，保存鉴权需求标记，重启时在凭据补齐前禁止监听，不自动降级免密。
同一数据目录通过 OS 文件锁排除第二个实例；真实 bind 检测端口冲突。
状态仅保存配置和本工具创建的 client_id。远端 token 只在内存中，重启先清理登记的旧资源再恢复 enabled 网关。
清理失败保留登记并退避，不调用全账号清空接口；创建请求超时不自动重放。

## 协议与部署边界

- 同端口：HTTP 请求、HTTP CONNECT（包括 HTTPS 目标）、SOCKS5 CONNECT TCP。
- 域名经上游解析，无绕过代理的直连回退。
- 本地加密 HTTPS 代理监听、SOCKS UDP/BIND 不属于此 TCP 网关实现。
- 普通 HTTP 按消息边界转发并关闭连接；CONNECT/SOCKS 隧道双向流转发。
- 管理 Web 仅回环监听，不作为互联网生产 Web 服务部署。
- 重建不保证新 IP；失效 TCP 流由客户端重试，不宣称会话无缝迁移。

## 官方依据

已读取 URnetwork SDK `api.go` 的 GetProviderLocations/FindProviderLocations，以及官方 Web `manager/src/services/api.ts` 的位置请求。
按官方接口返回统计，不用旧公开地图推断 App 的在线节点数；不同时间、筛选条件下数值可能不同。

- https://github.com/urnetwork/sdk/blob/main/api.go
- https://github.com/urnetwork/web/blob/main/manager/src/services/api.ts
- https://docs.python.org/3/library/asyncio-stream.html
- https://docs.python.org/3/library/http.server.html
- https://www.rfc-editor.org/rfc/rfc1928
- https://www.rfc-editor.org/rfc/rfc1929

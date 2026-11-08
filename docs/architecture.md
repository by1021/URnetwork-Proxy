# 当前架构

| 层 | 文件 | 职责 |
|---|---|---|
| 官方接口 | `core/client.py` | 认证、GET 401 有界重登、位置绑定创建、指定 client_id 回收；创建 POST 不盲重放 |
| 位置目录 | `core/locator.py` | provider-locations 全量国家、find-provider-locations 地区/城市搜索；精确 ID、父级筛选、有界缓存及过期状态 |
| 数据面 | `core/multiplexer.py`、`core/protocol.py` | asyncio 单 TCP 端口识别 HTTP / SOCKS5；鉴权、完整握手、背压、半关闭 |
| 健康检查 | `core/checker.py` | 有界 HTTP / SOCKS5 / HTTPS 协议探测 → 各成功协议出口 IP → 证书校验的 HTTPS 国家查询；国家与来源最多缓存 5 分钟，明文 ISP 数据不参与国家判定 |
| 生命周期 | `core/gateway.py` | 默认三节点主备池、内存选路、后台补池/修复、配置 generation、所有权登记、定向清理及账户级限速 |
| 本地管理 | `core/web_server.py` | 事件循环桥接、回环 REST API、管理会话、CSRF、配置面板 API、静态资源 |
| 配置编辑 | `core/config.py`、`core/config_editor.py` | 字符串感知 JSONC 解析、字段校验、版本冲突检查、认证字段遮罩、旧凭据迁移 |
| 持久化 | `core/storage.py` | 原子替换、单进程 OS 文件锁；Web 面板与 CLI 共用 config.json |
| 前端 | `web/` | 无 CDN 中文管理页；轮询、搜索、CRUD、日志、配置表单与源码双模式 |
| 启动 | `main.py`、`start_web.bat` | 保留原 CLI；新增 Web 模式 |

## 运行交付结构

程序由 `main.py`、`core/` 和 `web/` 构成，启动脚本与 `systemd/` 提供运行入口；配置模板和使用文档随源码保留。自动化测试脚本与历史评审、验证产物不包含在交付目录中，运行链路不依赖这些内容。

## 数据流与不变量

客户端 → 固定本地端口 → 协议处理器 → 就绪代理池（主用优先）→ 目标。
每网关 `pool_size` 默认 3、范围 1–20；第一条验证就绪即可服务，后台补齐两条热备。主用建连失败时从内存候选切换，创建、探测、远端清理均不处于选路关键路径。每连接最多试三条候选，每条建连默认预算 3 秒，共享最多 9 秒预算；成功响应或业务数据提交后不重试。既有 TCP 流固定上游，不迁移、不重放请求。

国家、地区、城市绑定使用 `initial_device_state.location.connect_location_id.location_id`，所有池成员沿用同一精确 ID；显式位置不被住宅城市优选覆盖。管理面校验位置 ID、类型、国家及所选地区父级；CLI 同名搜索不直接取第一条。指定国家网关只发布各成功协议实测国家均匹配的候选，住宅 ISP 优选回退也不放宽国家约束。城市/地区是官方选路范围，不承诺每个外部 IP 库给出相同城市名；请求位置与探测的 `exit_city` / `exit_region` 分开展示。

池模式首次确认主用失败即尝试就绪热备；`pool_size=1` 保留原连续失败阈值。国家漂移或未验证出口立即撤下并定向断开该节点隧道，不受其他协议断线影响；普通同绑定换线保留其他健康节点的既有流。位置修改先验证新候选再提交并清理旧池；失败保留旧配置。缩容只退休多余备用，30 秒排空节点仍登记所有权并显示“回收中”，不计入 `pool_ready`。`pool_total` 含排空/待清理资源；`pool_status` 区分 ready/filling/depleted/stopped，健康主用的服务状态不被 `pool_error` 覆盖。

管理器使用单个 asyncio 循环；阻塞的官方请求在线程执行。URClient 的认证和请求由 RLock 保护；更新凭据时禁止并发网关操作。
后台补池与显式启动/检查共用本地绑定及鉴权前置条件，由独立 bind_lock 串行化；缺少恢复凭据或监听/TLS 失败时不创建后台资源、不谎报运行。端口或密码编辑同样使挂起 bind 失效，绑定完成后复核配置快照并关闭旧监听结果。建连中的任务在 await 前登记上游归属，向客户端提交成功/业务数据前复核候选 ID 与配置代次，退休节点的 pending 握手也被定向取消。
管理 API 对同一网关的 PUT 串行执行读取、合并、国家校验和提交；不同网关独立。高级本地代理密码仅驻留内存，保存鉴权需求标记，重启时在凭据补齐前禁止监听，不自动降级免密。
同一数据目录通过 OS 文件锁排除第二个实例；真实 bind 检测端口冲突。

`GET/PUT /api/config` 读写固定的 config.json；`POST /api/config/validate` 校验 JSONC 草稿，不写盘。均复用管理会话与 CSRF 保护。已有六类认证字段以保留占位符返回，响应与错误不回显旧凭据。编辑器以配置文件及旧凭据文件的摘要检测过期版本，写入前再次检查；原子写盘失败时保留原配置。

配置面板保存不热更新运行中客户端、不自动重启；显示磁盘配置与当前监听端口。认证/API 地址变化须先回收网关；保存后在重启前阻止新增旧账号网关。旧版 credentials.json 仅用于兼容，保存时迁入 config.json 并改名保留备份。表单与源码共享内存草稿，保留未知扩展字段，保存统一输出格式化 JSON；不保留源码注释/排版。
状态仅保存配置和本工具创建的 client_id。远端 token 只在内存中，重启先清理登记的旧资源再恢复 enabled 网关。
清理失败保留登记并退避，不调用全账号清空接口；创建请求超时不自动重放。旧 generation 的创建结果先登记再回收，旧探测结果不覆盖新绑定；停止/删除阻止补池并等待迟到资源清理。

账户能力从官方实时权益取得并短缓存，未知时保守处理。创建串行化，计入本工具拥有的活动/排空资源及已知外部资源；账户每分钟额度、每网关每小时 120 次本地上限和积压容量共同限制。Pro 采用 unique-exit auto；故障节点优先后台检查恢复/轮换，恢复宽限期不阻塞主备切换。供给不足、额度不足与 retry-after 驱动退避，不注销健康主用。
较早的成功探测不清除较新的账户拒绝期限；停止单个网关后迟到的服务器限流响应仍约束整个账户，不回写已停止网关状态。满额且无替换槽位时，仅有界回收本工具已确认坏节点；健康/外部资源及 unique-auto 宽限中的节点保留，回收失败继续登记并退避。

`GET /api/locations` 复用管理会话，支持 query、country_code、type、region_location_id；返回官方记录及 source/updated_at/stale/error。位置缓存最多 64 组，失败保留旧快照但明确过期。成员摘要只暴露 client_id、角色、探测结果及脱敏错误，不包含远端 token 或代理 URL。

## 协议与部署边界

- 同端口：HTTP 请求、HTTP CONNECT（包括 HTTPS 目标）、SOCKS5 CONNECT TCP。
- 域名经上游解析，无绕过代理的直连回退。
- 支持本地 TLS HTTPS 代理监听；SOCKS UDP/BIND 不属于此 TCP 网关实现。
- 普通 HTTP 按消息边界转发并关闭连接；CONNECT/SOCKS 隧道双向流转发。
- 管理 Web 仅回环监听，不作为互联网生产 Web 服务部署。
- 重建不保证新 IP；失效 TCP 流由客户端重试，不宣称会话无缝迁移。

## 官方依据

已读取 URnetwork SDK `api.go` 的 GetProviderLocations/FindProviderLocations，以及官方 Web `manager/src/services/api.ts` 的位置请求。
按官方接口返回统计，不用旧公开地图推断 App 的在线节点数；不同时间、筛选条件下数值可能不同。

位置请求与 hosted-proxy 契约依据 `urnetwork/connect@00d4f62b27861d40feef120393d2a1e99dbc2471/api/bringyour.yml`；限额及 unique-exit 依据 `urnetwork/server@c3997dc3aa763c8cfb4cfbaaea69a0bd795c9cfb/model/network_client_proxy_limit.go`、`model/proxy_unique_exit_model.go`（2026-11-08 核对）。

- https://github.com/urnetwork/sdk/blob/main/api.go
- https://github.com/urnetwork/web/blob/main/manager/src/services/api.ts
- https://docs.python.org/3/library/asyncio-stream.html
- https://docs.python.org/3/library/http.server.html
- https://www.rfc-editor.org/rfc/rfc1928
- https://www.rfc-editor.org/rfc/rfc1929

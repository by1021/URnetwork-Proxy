# 网关代理池与精确位置设计

path: System
req: docs/requirements/proxy-pool-location.md
base_commit: e2f3c820e1613036b376ee7b0bb891793a754abb
date: 2026-11-08

本仓库无 `.ai_state`，按现有约定直接使用 docs 记录设计，不初始化新状态机。跨模块实现采用独立写者与隔离 worktree；主线程整合，最终代码运行验证后独立审查。

## 当前 / 期望 / 不变行为

当前单 upstream，30 秒周期、3 次失败后创建替换；转发回调未调用，最多持有两个资源，切换取消全部隧道。城市 CLI 重新按名字搜索并取第一项，管理面仅开放国家。

期望一个固定端口维护默认 3 个就绪上游（1 主 2 备），候选校验后入池；故障切换从内存就绪列表完成，耗时 API 在后台。国家/地区/城市统一精确 ID 绑定。

不变：HTTP/HTTPS/SOCKS5、本地鉴权、国家各协议硬约束、创建 POST 不盲重放、所有权先登记后可取消操作、匿名凭据不落盘/不进入日志、账号资源清理隔离。

## 共享接口合同

- 配置新增 `pool_size: 3`（1–20）、`location_name: ""`、`region: ""`、`city: ""`、`region_location_id: ""`；`mode` 扩展为 `best|country|region|city`。best 清空位置字段；其余要求国家代码和精确 `location_id`。保留旧字段和状态文件兼容。
- 网关 snapshot 保留旧字段，新增 `pool_ready`、`pool_total`、`pool_status: ready|filling|depleted|stopped`、`pool_error`、`active_client_id`、`exit_city`、`exit_region`、`upstreams`。成员只暴露 client_id、角色/状态、实测出口/延迟/时间/错误，绝不暴露 auth_token 或上游 URL。
- 有健康主用时 `status=running`，补池错误放入 `pool_error`，不污染网关服务状态。无可用成员才进入服务异常；冷启动第一条就绪即返回，后台补余量。
- `URClient.create_proxy` 支持 region/city 精确 location_id，传入 `location_name/region/city/region_location_id` 元数据；精确 ID 优先，不二次按名称搜索。没有精确 ID 的 CLI 搜索应明确选择，不跨国取第一项。返回解析后的 location_id、country_code、location_type、exit 和 unique_exit 元数据。
- `URClient.get_proxy_capabilities()` 返回 `is_pro`、`unique_exit`、`active_limit`、`creates_per_minute`，从官方实时权益读取并有限缓存；未知时使用保守默认，不盲信 API Key 的 JWT payload。可通过 `list_proxies()` 获取账户资源统计与出口状态。嵌入式套餐/服务器未知额度不宣称已验证。
- 官方 API 错误仅暴露允许的结构化 code/retry-after，不回显响应体。明确供给不足/额度不足退避，健康线路保留。Pro 默认 `unique_exit_replace=auto`，不盲重放创建 POST。
- `GET /api/locations?query=...&country_code=...&type=region|city&region_location_id=...` 使用现有管理会话鉴权，返回 `locations/source/updated_at/stale/error`。国家接口保持兼容。服务端通过官方查询所得记录校验 ID、类型、国家及父级关系；缓存有界、过期可见。
- Relay 保持 get_upstream 与旧 on_result(bool) 兼容，可新增 get_candidates 和节点级结果回调；候选顺序主用优先。连接阶段在共同有界预算内试备选；成功响应/业务数据提交后绝不重试。连接任务按 client_id 追踪，健康切换仅定向 drain 退役节点。

## 生命周期与并发

远程申请、探测与清理不持有数据面切换锁。每次后台操作持有配置 generation；完成时再次校验，过期资源登记后回收，旧结果不覆盖新绑定。停止/删除阻止补池并回收全部成员及迟到候选。

目标池之外预留受限替换容量；draining/pending-cleanup 仍计入所有权与账户额度。全局创建队列尊重每分钟限制，清理失败不无限膨胀，也不阻塞已有成员切换。unique-exit 失效优先后台修复/轮换（尊重状态与限速），必要时重建；不把官方 120 秒恢复宽限期放进本地切换路径。

地区/国家修改先建立一个新绑定的验证候选，再提交配置并退役旧池；失败保留旧配置和旧池。减少 pool_size 只退役多余备用，主用保持；增加后台补齐。旧状态缺 pool_size 默认 3，旧测试显式 pool_size=1 保留单节点契约。

## 验收

- AC1: 新旧配置默认 pool_size=3；第一条就绪可服务，后台补齐；snapshot 无上游秘密，池不足不谎报三条健康。
- AC2: 已有 B/C 就绪时 A 拒绝连接或检测失败，从就绪成员切换而非等待 create；连续失去两条仍可服务；旧节点失败反馈不污染继任者。
- AC3: HTTP、HTTP CONNECT、HTTPS 代理和 SOCKS5 建连失败在业务提交前有限尝试备用；上传/POST 和已建立流不重放；目标拒绝、客户端断开/鉴权错误不直接全池熔断。
- AC4: 补池/检测/清理/慢创建不阻塞现有备用选择；正常换线不取消所有健康隧道；pool_size 调整、绑定修改失败回滚、stop/delete/inflight-create/restart 资源无遗漏且不越界清理。
- AC5: 所有国家/地区/城市候选使用相同精确 location_id；country_code 各成功协议硬约束不回退；城市/地区展示与请求位置分离；同名及跨国结果不选错。
- AC6: API Key 可从官方权益开启 Pro unique-exit，默认 auto；供给不足/额度不足/创建错误有脱敏结构化状态与账户级限速，保留健康主用且不重放创建 POST。
- AC7: 浏览器可创建/编辑池大小和国家/地区/城市；选择绑定校验、空结果/过期/失败状态明确；网关显示主备/可用数量，刷新和编辑不丢失用户选择。
- AC8: 现有单测及新增红绿行为测试通过；本地真实 TCP/HTTP/SOCKS5/TLS 故障切换与 Chromium 管理流程通过；架构/使用说明更新；独立 review 无 P0/P1。真实付费上游环境留为明确未运行，不替代本地证据。

## 写者责任

- pool writer: core/gateway.py、core/multiplexer.py、core/protocol.py、core/checker.py；相应池/数据面新测试以及既有 gateway/checker 单测。
- location/UI writer: core/client.py、core/locator.py、core/web_server.py、core/config.py、main.py、web/*；相应位置/client/API/UI 测试及既有 UI smoke 兼容。
- 主线程：设计、需求、路线图、整合、运行验证、文档、审查接回；共享字段依上方合同，不并行覆盖源码所有权。

## Done contract / 允许副作用

required: 本地临时目录、回环监听、隔离 mock 控制面＋真实 socket 数据面、Chromium。
advisory: 公开位置搜索；不使用真实账户凭据创建代理。
允许安装独立测试 venv 的 Playwright/Chromium，创建临时测试文件及测试服务；结束清理测试进程。不改 data/config.json、不重启生产容器、不推送。

## 官方依据

2026-11-08 已读取 Hosted proxies 文档及公开查询接口。固定源码：urnetwork/connect@00d4f62b27861d40feef120393d2a1e99dbc2471/api/bringyour.yml；urnetwork/server@c3997dc3aa763c8cfb4cfbaaea69a0bd795c9cfb/model/network_client_proxy_limit.go、model/proxy_unique_exit_model.go。公开搜索 Tokyo 同时返回城市/地区；California 返回美国及墨西哥位置，必须以 ID 和父级筛选。

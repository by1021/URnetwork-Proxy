# 新建网关端口与出口国家修复

Bugfix，2026-11-08。项目没有 `.ai_state`，直接工作，不初始化状态。

## 当前 / 期望 / 不变

| 项目 | 当前证据 | 期望 |
|---|---|---|
| 默认端口 | HTML、后端、当前 Docker 映射从 31000 起；打开表单的 JS 仍从 10801 起 | 新建从 31000 起，跳过已保存端口；编辑保持原端口 |
| 美国选择 | 现有网关保存 US，location_id 与官方国家目录 US 完全匹配 | 保留所选国家位置，不被住宅城市优选覆盖 |
| 出口验证 | 探针只验证连通性和 ISP；没有保留或校验实际国家 | 候选各协议的实测国家都匹配所选国家才发布；部分协议故障也验证其余可用出口 |
| 当前实测 | 排查时现有出口在 ip-api 和 ipinfo 均返回 US；没有现场复现历史 VN 出口 | 区分请求国家与实测国家，不将请求标签当作地理验证结果 |

按官方协议补齐 `initial_device_state.country_code`，保留
`location.connect_location_id.location_id` 与位置国家元数据。顶层 country_code
是缺少 location 时的官方回退，不把这一缺失直接认定为历史 VN 的唯一原因。

保留自定义端口、已保存网关、自动模式、代理鉴权、所有权清理和住宅 ISP
优选的既有兼容行为；不清空账号资源或重写用户配置。国家验证与住宅优选
独立，任何住宅优选回退也须通过国家校验。

国家硬约束只采用证书校验后的 HTTPS 地理查询；原有 ip-api HTTP 数据仅
用于 ISP 优选，不参与国家判定或国家缓存。协议探测共用每协议的有界时间
预算，避免一个故障协议掩盖另一个可用出口。没有任何可用出口的完全断线
仍沿用原连通性失败阈值；有可用出口但国家漂移或未验证时立即撤下上游。

## 验收

- AC1: 浏览器新建默认端口为 31000，跳过已保存端口；编辑旧端口不改值；JS 资源版本同步更新。
- AC2: 显式选择 US 位置且开启住宅优选时，创建请求仍使用原 US location_id；顶层及位置国家代码一致；返回已解析的真实选择参数；自动模式不带指定国家。
- AC3: 探针经证书校验的 HTTPS 地理接口获取国家代码与来源，不信任明文或降级响应；HTTP、SOCKS5、HTTPS 的任一成功协议都分别验证出口，不被其他协议故障掩盖；暂时失败不永久缓存。
- AC4: 创建、换线、修改国家与恢复时，VN 或未知国家候选不得进入 US 网关（包含住宅优选回退）；失败有明确错误与有界退避；合格的旧上游在失败换线时保留。
- AC5: 运行中实测国家漂移时立即停止发布旧上游并断开旧隧道，只恢复同国候选；自动恢复关闭时不自行创建；界面分开显示请求国家与实测国家。
- AC6: 回归单测、真实浏览器表单/API 验证通过；运行资源更新前确认短暂断线影响，不删除用户数据。

## 官方依据

- [URnetwork SDK proxy_config.go](https://github.com/urnetwork/sdk/blob/1adb7752f2a67a1026ccc9fecacb983c0dbea667/proxy_config.go)：ProxyDeviceState 国家字段与创建请求模型。
- [URnetwork 服务端 network_client_model.go](https://github.com/urnetwork/server/blob/c85ba930b87eaac6982110150998db7e1193a267/model/network_client_model.go)：ExtendedProxyDeviceState、缺少 location 时按 country_code 回退。
- [ip-api JSON API](https://ip-api.com/docs/api:json)：ISP / hosting 元数据，仅作为住宅优选参考。
- [IPinfo 官方 API 文档](https://ipinfo.io/developers)：HTTPS 地理查询及 `country` 字段；国家判定使用该来源。

## 验证记录

基线：`athena run -- python3 -m unittest discover -s tests -v`，19 项通过。
无活动 sprint，Athena 提示不记录门禁证据；不伪造 sprint 或门禁通过状态。

| 场景 | 命令 / 证据 | 结果 |
|---|---|---|
| 修复前复现 | 新增回归测试后跑同一单测命令 | 39 项中 19 项失败：接受 VN、丢失国家数据、覆写显式位置等 |
| 修复前浏览器 | `tests/gateway_ui_smoke.py` | 保存 31000 与旧 10801 后，新建仍填 10802，期望 31001 |
| 独立审查回归复现 | 同一单测命令，增加部分协议故障、超时与明文国家伪造样本 | 51 项中 9 项失败、1 项错误；修复前记录已保留 |
| Python 3.10 超时复现 | 注入旧版独立 `asyncio.TimeoutError` 类型，再跑单测 | 52 项中 1 项错误；修复前记录已保留 |
| 修复后单测 | `athena run -- python3 -m unittest discover -s tests -v` | 52 项通过（宿主 Python 3.13），含独立审查边界回归 |
| 容器兼容 | 重建后的 Python 3.12 镜像只读挂载测试，禁用外部网络后跑相同单测 | 52 项通过，不挂载用户运行数据 |
| 最低版本兼容 | 官方 Python 3.10.22 镜像只读挂载源码和测试、禁用外部网络 | 52 项通过，验证旧版 `asyncio.TimeoutError` 的真实实现 |
| 真实浏览器 | Playwright 环境执行 `tests/gateway_ui_smoke.py` | 默认端口、旧端口编辑、表单校验、US 位置传递、VN 拦截、失败保存/换线提示和实测国家展示通过 |
| 现有网关只读查询 | 经现有本地网关分别发送 HTTP / SOCKS5 TLS IP 探针；ip-api、ipinfo 查询国家 | 排查时均为 US；这不是对历史 VN 的现场复现 |
| JS 语法 | `athena run -- node --check web/app.js` | 通过 |

测试仅使用临时目录与本地样本上游，不创建用户账号的测试代理。
浏览器测试是可选开发依赖，不加入运行依赖。运行服务更新须先确认短暂断线；
配置内容、已有网关绑定和数据目录不作为修复手段改写。

# 控制台登录与运行 IP

日期：2026-10-31。Feature（管理模块）；仓库没有 `.ai_state`，按约定直接工作，不初始化状态。

## 需求与边界

用户要求将当前登录替换为用户名/密码，默认 `admin / admin`；启动后和前端均显示自动识别的运行环境 IP，消除地址占位提示。保留已有 IPv4/IPv6 监听配置、上游凭据与代理功能，不改用户 `config.json`，不重启正在运行的服务。

## 交付切片

1. 账号密码登录：独立 `web_username` / `web_password`，沿用会话、限速、CSRF、退出机制；删除令牌登录入口。配置面板可修改，重启生效，密码遮罩。
2. 运行 IP：启动时读取系统 IPv4/IPv6 出站路由选择的本机地址，失败时回退明确的监听 IP / 回环；同一后端快照用于启动日志、登录页、控制台及配置面板。地址列表按实际监听协议生成，IPv6 URL 加方括号。

## 验收

- AC1: 未配置管理账号时 `admin / admin` 登录成功；错误、缺失、非文本凭据和旧令牌登录失败；自定义账号替代默认账号。
- AC2: HttpOnly/SameSite Cookie、会话恢复、过期、退出撤销、CSRF、跨源拒绝和失败限速保留；密码不写入 URL、浏览器存储、启动日志或配置读取响应。
- AC3: 管理账号与 URnetwork 上游账号独立；配置密码遮罩保留、修改、校验与重启生效，原配置文件不被自动修改。
- AC4: 实际 IPv4/IPv6 自动识别、单栈与离线回退可测；日志和前端使用相同快照，无 `[IP]` 等地址占位；回环监听不宣称支持远程访问。
- AC5: 真实 HTTP 与 Chromium 验证正常/错误登录、刷新保留会话、退出、IP 显示、移动端布局；完整单测回归通过。

## IP 语义

运行 IP 是服务所在主机/容器的网卡路由地址，不是代理出口 IP，也不保证是 NAT 后的公网地址。不调用外部 IP 查询服务、不改变监听范围或代理地址；局域网、容器地址会如实显示。公网 NAT 映射不在本次自动识别范围。

## 验证证据

| 范围 | 检查 | 结果 |
|---|---|---|
| AC1–4 / 全量回归 | `athena run -- python -m pytest -q` | 121 passed，67 subtests passed |
| AC1–2 / AC4–5 | `athena run -- env CHROMIUM_PATH=/usr/bin/chromium SCREENSHOT_DIR=/tmp/urnetwork-login-runtime-evidence python -m tests.browser_smoke` | Chromium：错误/正常登录、会话刷新、退出、真实 IP、网关 CRUD、移动端均通过 |
| AC3 / AC5 | `athena run -- env CHROMIUM_PATH=/usr/bin/chromium SCREENSHOT_DIR=/tmp/urnetwork-login-runtime-evidence/config python -m tests.browser_config` | 管理账号编辑、密码遮罩、磁盘保存、原配置编辑回归、移动端均通过 |
| AC4 | 临时空状态启动真实 `serve({}, host="::", ...)`，读取 `/api/runtime` 并登录后以 SIGINT 关闭 | 实际 IP / 日志 / API 一致，IPv4/IPv6 访问链接格式正确，干净退出 |
| 静态检查 | `node --check web/app.js`、`python -m compileall -q core main.py tests`、`git diff --check` | 均通过 |

已先验证旧实现的用户名/密码登录与运行信息接口测试失败，再实现并通过；无效管理配置的启动锁释放也有红/绿回归。浏览器截图在临时证据目录，未覆盖原有文档资产。用户配置、既有网关状态和正在运行的服务均未修改。

当前无 `.ai_state`；`athena run` 已执行检查但提示未记录 sprint 证据，`athena review prepare` 同样提示未初始化。独立只读审查使用外置材料包，不宣称生成 Athena 门禁凭证。

## 独立审查修正

首轮审查复现三项边界问题，均补充失败回归后修正：

- 管理模式严格读取并校验配置，损坏文件停止启动，不启用默认账号；原 CLI 回退行为保持兼容。
- 访问 URL 与管理请求共用 Host 白名单匹配逻辑；白名单只有域名时显示域名链接，无匹配时明确提示，不自动打开被拒地址。
- 上游账号热更新仅替换上游运行参数，保留当前管理账号和监听参数快照；待重启的管理密码仍显示重启提示。

复核补充：白名单仅追加 DNS 别名，字面 IP 继续受实际绑定与协议族限制；IPv4-only、IPv6-only、特定地址绑定、通配监听及无匹配地址五类回归通过。

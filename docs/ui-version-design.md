# 网关地址实际入口核验与前端版本识别

Bugfix，延续网关复制地址任务；无 `.ai_state`，直接工作，不初始化。保留已有改动与运行服务。

## 当前 / 期望 / 不变

- 用户仍看到前轮旧提示和旧 IP。实测当前运行进程位于本仓库，域名 8777 返回的 app.js 与工作区 SHA-256 一致，含 location.hostname，新代码不含用户贴出的旧提示。原页面对 JS 使用无版本资源 URL，未提供前端版本标识；尚未确定用户旧页面是未刷新标签页、中间缓存还是不同入口。
- 在实际入口打开并登录，真实网关 HTTP/SOCKS5 两个复制动作均已输出当前域名和 1080。账号/国家等上游请求在测试浏览器被阻断，未运行网关写操作；用户 config.json、gateways.json 的哈希前后相同。
- 为 JS 入口增加显式版本，旧无版本资源不会命中新入口；由实际执行的 JS 标注版本，在登录页与控制台可见，便于用户确认当前加载内容。
- 不改变后端监听、配置、代理出口或账号机制，不重启运行服务；只更换前端资源引用与版本显示。已打开的旧标签页仍需重新加载。

## 验收

- AC1: 页面请求带版本的 app.js，由执行 JS 渲染版本标识；加载旧 HTML 时新 JS 不因缺少标识元素而出错。
- AC2: 即使浏览器对无版本 /app.js 接收旧脚本，当前 HTML 加载的带版本资源仍生效；IPv4/IPv6、域名、HTTP/HTTPS 和转发端口下复制行为保持正确。
- AC3: 实际正在运行的域名入口展示新版本，真实网关 HTTP/SOCKS5 复制为当前访问主机加网关端口；保存裁剪证据，不泄漏凭据，不修改已有网关配置。

## 验证记录

| 范围 | 命令 / 证据 | 结果 |
| --- | --- | --- |
| 红 | `tests.browser_addresses`，对无版本 app.js 返回模拟旧脚本 | 登录版本断言失败；`/tmp/urnetwork-ui-version-red.log` |
| AC1–2 | `athena run -- env CHROMIUM_PATH=/usr/bin/chromium SCREENSHOT_DIR=/tmp/urnetwork-ui-version-evidence python -m tests.browser_addresses` | 7 场景通过；确认请求带版本 URL、旧脚本未执行、登录页/页脚版本及 HTTP/SOCKS5 复制值正确；`/tmp/urnetwork-ui-version-browser.log` |
| AC1 兼容 | 源码检查 | 版本标识使用 querySelectorAll，旧 HTML 无对应节点时空遍历，不增加必需 DOM 节点 |
| AC3 实际服务 | `athena run -- python /tmp/urnetwork-live-copy-check.py` | 实际域名 8777、新版本资源与标识、真实网关两个复制动作通过；`/tmp/urnetwork-live-copy-version.log` |
| 全量 | `athena run -- bash -c 'node --check web/app.js && python -m pytest -q && git diff --check'` | 125 passed、67 subtests passed；`/tmp/urnetwork-ui-version-tests.log` |

运行进程未重启，仍为本轮开始时的进程；用户配置和既有网关文件哈希前后相同。真实管理浏览器仅做本地登录、查询、复制、退出；账号/国家的上游请求在浏览器被阻断，未修改网关。真实页截图已裁剪为地址区域：`docs/assets/gateway-copy-live.png`。

实际 `1080` 仍只绑定回环，复制地址修正不意味着远程代理直连已经生效。本轮没有调整监听、安全边界或端口转发。旧页面来源尚未确定；已确认当前域名 8777 返回新代码，不能把旧提示直接归因为浏览器缓存。

没有 `.ai_state`，Athena 检查执行但不生成 sprint/门禁凭证；独立审查使用外置材料包。

两套既有 Chromium 回归 `tests.browser_smoke` / `tests.browser_config` 通过，无 JS/CSP 错误；日志 `/tmp/urnetwork-ui-version-regression.log`。

独立审查返回 `VERDICT: PASS`，AC1–AC3 无需修复项；审查方独立复跑 7 个 Chromium 场景与静态检查通过，另核对实际入口脚本/日志/截图。原样输出 `/tmp/urnetwork-ui-version-review-result.txt`。审查期间源码未修改。

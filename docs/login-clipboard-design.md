# 登录页精简与代理一键复制

Bugfix；无 `.ai_state`，按仓库约定直接工作，不初始化状态。不覆盖已有工作区改动。

## 当前 / 期望 / 不变

- 当前：登录页包含主机、版本、默认账号说明；复制仅调用 Clipboard API，普通 HTTP 或权限拒绝后立即要求手动复制，地址本身不可点击。
- 期望：登录页保留品牌、标题、账号密码、登录按钮和必要的状态/错误；代理地址整行点击、触摸或键盘操作即可复制完整 URL。优先 Clipboard API，缺失或失败时自动使用兼容复制，确实失败才保留已选中的手动兜底。
- 不变：保留会话恢复、登录错误、退出和认证校验；保留控制台内的运行诊断和界面版本；代理协议、浏览器访问主机、网关端口和凭据编码不变。不改后端、配置、网关状态。部署需用户确认，未确认前不重启现有服务。

## 验收

- AC1: 登录页不再显示主机、版本和默认账号说明；320px 手机到桌面无横向溢出，输入字号至少 16px、提交按钮至少 44px；错误登录、成功登录、刷新恢复和退出保持正常。
- AC2: HTTP、HTTPS、SOCKS5 地址整行均为原生按钮，支持点击、触摸、Enter/Space，触摸高度至少 44px；剪贴板中是完整协议 URL，主机/网关端口、IPv6 方括号和凭据编码保持正确。
- AC3: Clipboard API 正常、缺失（真实普通 HTTP）和拒绝三种路径均自动复制并提示成功，无手动弹窗；兼容复制后清除临时节点并恢复焦点、选择和滚动位置。
- AC4: 两种复制方式都失败时不提示成功，弹窗显示完整地址并选中；资源版本同步更新，浏览器回归、既有 UI 冒烟和全量单测通过。

## 验证方式

使用临时配置、确定性上游和独立管理 HTTP 服务；Chromium 桌面/触摸视口验证真实剪贴板、非安全 HTTP、拒绝及失败路径。手机 Safari 实机表现待验证，不把 Chromium 触摸仿真等同于实机测试。

## API 依据

- [Clipboard.writeText](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText)：安全上下文限制，写入可因权限被拒。
- [Document.execCommand](https://developer.mozilla.org/en-US/docs/Web/API/Document/execCommand)：已弃用，仅用于普通 HTTP 和 Clipboard API 失败后的兼容兜底；检查返回值，并保留失败处理。
- [HTMLTextAreaElement.setSelectionRange](https://developer.mozilla.org/en-US/docs/Web/API/HTMLTextAreaElement/setSelectionRange)：显式设置完整选区；只读临时文本框避免手机弹出键盘。

## 验证结果

- 先红：原界面在登录精简和普通 HTTP 自动复制两组测试中失败，手机/桌面均退回手动弹窗。
- 后绿：`tests/login_clipboard_ui_smoke.py` 7 组通过，覆盖真实剪贴板、普通 HTTP、权限拒绝、IPv6、凭据编码、键盘/触摸、焦点/选区/滚动恢复和失败兜底；无浏览器脚本错误。
- 回归：全量 52 项 Python 单测、既有 `tests/gateway_ui_smoke.py`、JS 语法和 diff 空白检查通过。
- 日志及桌面/手机截图位于 `.local/login-clipboard/`。`athena run` 已执行检查；仓库无活动 sprint，CLI 未记录 Athena 门禁证据，不宣称已通过状态机门禁。

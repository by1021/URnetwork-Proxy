# 配置面板：需求与验收

状态：已实现并验证 · 2026-10-31 · 单模块 Feature。工作区未启用 `.ai_state`，未自动初始化。

## 目标与范围

把“连接设置”改成对应 `config.json` 的配置面板：可视化表单与 JSONC 源码双模式，保留代理维护入口。原有网关生命周期、代理端口和国家排序不变。

- AC1：认证、API 地址、主站地址、超时、默认模式/国家、Web 端口均可编辑；源码与表单切换不丢字段。
- AC2：保存写入项目 `config.json`，重新加载可读到；既有凭据以占位符保留，不发送原值至浏览器；清空需明确编辑。
- AC3：支持 JSONC 注释/尾逗号输入；验证字段类型、范围、URL 和重复键；失败不写盘，源码错误不丢草稿。
- AC4：检测磁盘外部修改/多标签页版本冲突；显示未保存状态，刷新、离开、退出前提示；保存后明确重启生效。
- AC5：兼容旧 `.local/credentials.json`，保存时迁入 config 并留备份；账号或 API 地址变更前要求网关和旧资源回收完成。
- AC6：认证/CSRF 保护、原子写入、桌面/移动端、深浅主题、真实浏览器交互均有自动化证据；测试使用临时配置和模拟上游。

## 实现约定

- `GET/PUT /api/config`，并提供只解析/校验的 `POST /api/config/validate`。固定配置文件路径，客户端不能指定路径。
- 已有敏感字段使用 `__KEEP_EXISTING__` 占位；空字符串显式清除，省略已有敏感字段时拒绝保存，避免意外删除。
- 返回格式化且遮罩的配置源码，保存统一规范化为 JSON（JSONC 的子集），不保留注释/排版；保留未知扩展字段。
- 修改仅落盘，不自动重启、不调用云端测试登录，不触碰已有连接；旧账号切换保护仍执行。
- UI 草稿仅驻留内存，不写浏览器存储。新端口与当前实际监听端口分开显示。
- 源码文本为扩展字段的权威表示，表单只提交已知字段补丁；避免 JavaScript Number 将大整数改写。模式切换通过服务端校验同步。

## 技术依据

Python `json` 的 `object_pairs_hook` / `JSONDecodeError`（官方文档）；Playwright Python locators 与 assertions（官方文档）。JSONC 预处理由本项目字符串感知扫描器实现。

## 验证记录

| 验收 | 证据 |
|---|---|
| AC1–AC2 | `python -m tests.browser_config`：表单/源码双向切换、真实临时文件保存、认证替换/保留、扩展字段及大整数精确保留 |
| AC3–AC5 | `python -m pytest -q`：98 passed；JSONC、重复键、字段边界、版本冲突、写入失败不覆盖、旧凭据迁移与备份、账号资源保护 |
| AC6 | 两个真实 Chromium 脚本通过：`python -m tests.browser_config`、`python -m tests.browser_smoke`；桌面/移动端、深浅主题、无 JS/CSP 错误；`node --check web/app.js` 与 `git diff --check` 通过 |

验证命令通过 `node C:/Users/Administrator/.athena/current/cli.cjs run -- ...` 执行；无活跃 sprint，CLI 未记录 `.ai_state` 门禁证据，未伪造状态。测试仅使用临时文件和模拟上游，没有执行真实账号清理、登录或重启。

独立 reviewer 初审发现并已修复：扩展字段大整数失真、Windows CRLF 大小边界、对象请求重复键漏检。修复后独立复核 `VERDICT: PASS`；独立复跑 98 项单测及三项复现通过。

复核源码/测试范围：core/config.py、core/config_editor.py、core/storage.py、core/web_server.py、web/app.js、web/index.html、web/style.css、tests/test_config_editor.py、tests/test_web.py、tests/browser_config.py。按排序文件名 + NUL + 文件字节 + NUL 计算 SHA256：`b57fe6ff28ff8201d14a325bc456264112575e682722192fc47ca72f0820f9ae`。

截图：`docs/assets/config-panel.png`、`docs/assets/config-panel-source.png`、`docs/assets/config-panel-mobile.png`。

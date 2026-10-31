# 独立审查入口

工作区：当前 URnetwork-Proxy 项目，基线提交 7625f1b。实现已整合回主项目，临时工作区已移除。
项目没有 .ai_state 且未获初始化许可；使用普通代码审查记录，不伪造 Athena 门禁结果。

需求与验收：docs/design.md AC1–AC7。实现架构：docs/architecture.md；操作说明：docs/gateway-guide.md。
审查所有新增 core/ 模块、修改的 client/checker/locator/main、web/、tests/，不读取或输出 config.json 中凭据。

关注：混合协议/流量正确性，国家绑定一致性，恢复重建/停止/删除竞态，所有权清理，配置原子性，认证与 CSRF、凭据脱敏，用户已有配置不被覆盖。

已执行验证：主项目 69 项单元/真实socket/管理HTTP API 测试通过；真实云端临时代理经同端口 HTTP/SOCKS5 curl 成功并回收。主项目 Chromium 验证登录、全量国家、双网关、编辑/启停/检测/换线/删除、缓存过期与恢复、深浅色/移动布局和注销通过，无 JS/CSP 错误。
命令：python -m unittest discover -s tests -v；python -m compileall -q core main.py。

只读返回严重性、文件行号、复现/修复建议以及 VERDICT。不可修改源文件或提交。

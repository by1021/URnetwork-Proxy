- [P1] core/multiplexer.py:232 — 强制选择 HTTPS 上游破坏明文 CONNECT/SOCKS5 半关闭：客户端发送 `upload` 后 `write_eof()`，等待 EOF 才回应的上游收不到结束信号，最终超时并丢失响应；相同测试用明文上游返回 `reply:upload`。须保留原有半关闭语义并补回归测试。
- [P2] core/gateway.py:688 — 重绑定遗漏 TLS 错误分类：使用显式证书启动网关后删除该临时证书，再编辑为空闲端口，实际返回 `local_bind_failed`，误导用户检查端口；此处分开捕获 `TLSConfigurationError` 并返回 `local_tls_failed`。

VERDICT: REWORK
AC1：创建、编辑、字段校验已覆盖；重绑定错误分类见 P2。
AC2：DEVIATED，HTTPS 上游破坏半关闭上传。
AC3：证书配置、信任、实例隔离及私钥权限已覆盖。
AC4：浏览器及真实出口证据已核阅；独立实跑 Python 3.10、3.13，各 146 项通过。

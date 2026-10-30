# URnetwork-Proxy 住宅代理管理器

基于 [URnetwork (ur.io)](https://ur.io) 去中心化住宅级隐私网络的 Python 管理工具。支持实时拉取全球 90+ 个国家的住宅节点分布，快速生成标准格式的 HTTP、HTTPS 与 SOCKS5 代理，并提供端到端握手自检与即用型测试命令。

---

## 特性亮点

- **零外部依赖**：基于 Python 3.10+ 标准库实现，无需 `pip install` 任何第三方包，下载即用。
- **实时地理感知**：每次运行均可直接从 `https://ur.io` 和官方 API 实时拉取最新支持的国家、城市及在线 Provider 节点数量。
- **三大路由策略**：
  - ⚡ **全球最佳链路（Best Available）**：系统自动优选当前全球延迟最低、最稳定的住宅出口。
  - 🌍 **指定国家（Country Code）**：支持指定 ISO 两位国家代码（如 `US`、`JP`、`DE`、`GB`、`SG`、`HK` 等）。
  - 🔍 **指定具体城市（City Search）**：通过模糊搜索指定全球任意热门城市（如 `Tokyo`、`Frankfurt`、`London` 等）。
- **标准格式与防降级优化**：默认填充 `none` 密码，避免 Python PySocks / Clash / sing-box 因空密码降级为无鉴权而导致连接被拒。
- **端到端出口 IP 深度自检**：生成后自动通过 HTTP / SOCKS5 隧道握手验证鉴权有效性，并输出真实住宅出口 IP 与延迟。
- **全协议同开**：URnetwork 底层为每个代理同时开放 HTTP、HTTPS 与 SOCKS5 三个独立端口。
- **网页端 PROXIES 同步管理**：支持查看官网网页端所有已创建的代理列表，并支持一键精准清理（调用 `/network/remove-client` 同步清空官网抽屉列表）。

---

## 目录结构

```text
URnetwork-Proxy/
├── config.json          # 助记词与 API 核心配置文件
├── main.py              # CLI 命令行与交互式菜单主入口
├── requirements.txt     # 依赖说明（免安装）
├── core/
│   ├── __init__.py
│   ├── config.py        # 配置加载模块
│   ├── client.py        # 官方 API 交互客户端（登录/配额/申请代理/清理/生成测试命令）
│   ├── locator.py       # 实时从 ur.io 抓取国家与节点分布搜索器
│   └── checker.py       # 包含 TCP 探测与 HTTP/SOCKS5 端到端出口 IP 深度握手自检
└── README.md            # 项目说明文档
```

---

## 快速上手

### 1. 配置助记词

编辑 `config.json`，填入你的 URnetwork 24 位助记词：

```json
{
  "seedphrase": "你的24位助记词...",
  "api_base_url": "https://api.bringyour.com",
  "ur_base_url": "https://ur.io",
  "default_mode": "best",
  "default_country": "US",
  "timeout_seconds": 15
}
```

### 2. 交互式菜单模式（推荐）

直接运行主脚本：

```bash
python main.py
```

终端将显示操作菜单：
```text
请选择操作：
 [1] ⚡ 自动选择最佳连接 (Best Available)
 [2] 🌍 快速选择热门国家 (US, JP, DE, GB, SG, HK...)
 [3] 📋 实时从 https://ur.io 拉取完整国家列表并选择
 [4] 🔍 搜索具体城市/地区 (如 Tokyo, Frankfurt, London...)
 [5] 📊 查看当前账号配额与使用量
 [6] 📑 查看当前网页端活跃代理列表 (PROXIES)
 [7] 🧹 清理并重置所有代理与历史客户端 (完全清空网页 Proxies)
 [0] 退出
```

### 3. 命令行静默调用模式

```bash
# 申请美国国家节点并自检
python main.py --mode country --target US

# 查看网页端当前活跃代理列表
python main.py --list-proxies

# 清理所有网页端代理与历史连接
python main.py --clean

# 查看账户余额与配额
python main.py --status
```

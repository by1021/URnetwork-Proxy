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
- **即用型测试命令生成**：控制台直接给出包含完整鉴权的 cURL 与 Python 单行测试命令，开箱即测。
- **账号配额监控与自洁**：一键查看每日配额使用情况，支持一键清理历史闲置客户端释放并发槽位。

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
  "seedphrase": "believe charge pond glimpse wire cargo soldier bone nice office useful olive sense need guitar club reduce ability brain walnut hurdle manage better rapid",
  "api_base_url": "https://api.bringyour.com",
  "ur_base_url": "https://ur.io",
  "default_mode": "best",
  "default_country": "US",
  "timeout_seconds": 15
}
```

### 2. 交互式菜单模式（推荐）

直接运行主脚本，将出现直观的交互菜单：

```bash
python3 main.py
```

终端交互菜单示例：
```text
======================================================================
         URnetwork 去中心化住宅代理生成与管理工具 (ur.io)
======================================================================

请选择操作：
 [1] ⚡ 自动选择最佳连接 (Best Available)
 [2] 🌍 快速选择热门国家 (US, JP, DE, GB, SG, HK...)
 [3] 📋 实时从 https://ur.io 拉取完整国家列表并选择
 [4] 🔍 搜索具体城市/地区 (如 Tokyo, Frankfurt, London...)
 [5] 📊 查看当前账号配额与使用量
 [6] 🧹 清理历史闲置客户端 (释放并发槽位)
 [0] 退出
----------------------------------------------------------------------
```

---

### 3. 命令行调用模式

通过 CLI 参数可直接集成到已有自动化流水线中：

```bash
# 1. 自动优选最佳节点（含端到端连通性自检）
python3 main.py --mode best

# 2. 快速生成且跳过端到端自检（极速模式）
python3 main.py --mode best --no-check

# 3. 指定国家生成（如日本 JP、美国 US、德国 DE）
python3 main.py --mode country --target JP

# 4. 指定城市生成（如东京 Tokyo）
python3 main.py --mode city --target Tokyo

# 5. 查看从 https://ur.io 实时抓取的全球支持国家及节点数
python3 main.py --list-countries

# 6. 查看当前账号配额与剩余流量
python3 main.py --status

# 7. 一键清理历史客户端释放并发槽位
python3 main.py --clean
```

---

## 输出格式说明

每次成功分配代理节点后，控制台将输出以下格式：

```text
======================================================================
【节点分配成功 - 出口策略: 全球最佳链路 (自动优选)】
----------------------------------------------------------------------
【标准格式代理 (用户名:密码@Host:端口)】
  HTTP 代理:   http://06GOOC...:none@fireside.bringyour.com:7154
  SOCKS5 代理: socks5://06GOOC...:none@fireside.bringyour.com:7153
  HTTPS 代理:  https://06GOOC...:none@fireside.bringyour.com:7155
  * 提示: 密码统一填充为 'none'，避免客户端因空密码降级导致鉴权被拒。
----------------------------------------------------------------------
【即用型测试命令 (可直接在终端中复制运行)】
  cURL (HTTP):     curl -s -x "http://06GOOC...:none@fireside.bringyour.com:7154" https://api.ipify.org
  cURL (SOCKS5):   curl -s --socks5-hostname "06GOOC...:none@fireside.bringyour.com:7153" https://api.ipify.org
  Python (HTTP):   python -c "import requests; print('HTTP IP:', requests.get('https://api.ipify.org', proxies={'http': 'http://06GOOC...:none@fireside.bringyour.com:7154', 'https': 'http://06GOOC...:none@fireside.bringyour.com:7154'}, timeout=10).text.strip())"
  Python (SOCKS5): python -c "import requests; print('SOCKS5 IP:', requests.get('https://api.ipify.org', proxies={'http': 'socks5://06GOOC...:none@fireside.bringyour.com:7153', 'https': 'socks5://06GOOC...:none@fireside.bringyour.com:7153'}, timeout=10).text.strip())"
----------------------------------------------------------------------
【官方原生返回 URL】(注意: 原生 URL 默认不含 Basic 鉴权信息)
  原生 HTTPS:  https://06gooc...fireside.bringyour.com:7155
  原生 HTTP:   http://fireside.bringyour.com:7154
  原生 SOCKS5: socks5h://fireside.bringyour.com:7153
----------------------------------------------------------------------
[*] 正在执行端到端代理隧道握手与出口 IP 自检 (基于 api.ipify.org)...
  HTTP 代理状态:   [在线 [OK] (1250.0 ms) -> 出口 IP: 167.160.88.225]
  SOCKS5 代理状态: [在线 [OK] (980.5 ms) -> 出口 IP: 167.160.88.225]
======================================================================
```

| 元素 | 说明 |
| :--- | :--- |
| **用户名** | 分配的专属 `auth_token` |
| **密码** | 固定填充 `none`（满足 RFC 1929 规范，避免客户端将空密码误判为无鉴权） |
| **Host** | 官方原生中继主机（如 `fireside.bringyour.com` 或 `crisp.bringyour.com`） |
| **HTTP 端口** | 常规 HTTP/Web 代理端口 |
| **SOCKS5 端口** | SOCKS5 套接字代理端口（需配合 remote DNS / `socks5-hostname`） |
| **HTTPS 端口** | 原生 TLS 加密代理端口（可将 Token 作为二级域名直连） |

---

## 客户端与代码调用示例

### 1. cURL 调用

```bash
# 使用 HTTP 代理
curl -v -x "http://<TOKEN>:none@fireside.bringyour.com:7154" https://api.ipify.org

# 使用 SOCKS5 代理（推荐 --socks5-hostname 进行远端 DNS 解析）
curl -v --socks5-hostname "<TOKEN>:none@fireside.bringyour.com:7153" https://api.ipify.org
```

### 2. Python Requests 调用

```python
import requests

TOKEN = "<你的TOKEN>"
HOST = "fireside.bringyour.com"
HTTP_PORT = 7154
SOCKS_PORT = 7153

# HTTP 代理
proxies_http = {
    "http": f"http://{TOKEN}:none@{HOST}:{HTTP_PORT}",
    "https": f"http://{TOKEN}:none@{HOST}:{HTTP_PORT}"
}
resp = requests.get("https://api.ipify.org", proxies=proxies_http, timeout=10)
print("HTTP 出口 IP:", resp.text.strip())

# SOCKS5 代理
proxies_socks5 = {
    "http": f"socks5://{TOKEN}:none@{HOST}:{SOCKS_PORT}",
    "https": f"socks5://{TOKEN}:none@{HOST}:{SOCKS_PORT}"
}
resp = requests.get("https://api.ipify.org", proxies=proxies_socks5, timeout=10)
print("SOCKS5 出口 IP:", resp.text.strip())
```

---

## 常见问题 (FAQ)

### Q1：为什么 SOCKS5 密码必须填 `none` 不能留空？
根据 RFC 1929 规范，SOCKS5 用户名/密码认证协议中密码长度需大于 0。在 Python requests (PySocks)、Clash、sing-box 等客户端中，密码为空时会放弃账号密码认证，回退为匿名无鉴权（0x00），导致 URnetwork 服务端直接拒接断开（报错 `All offered SOCKS5 authentication methods were rejected`）。填入 `none` 即可确保严格协商通过。

### Q2：为什么不能直接复制「原生 HTTP / SOCKS5」地址使用？
官方 API 返回的 `http_proxy_url` 与 `socks_proxy_url` 仅包含 `Host:Port`，不包含分配给你的鉴权 Token。若未带鉴权直接连接，中继服务器将静默超时或拒绝连接。请务必使用上方生成的【标准格式代理】或【即用型测试命令】。

### Q3：为什么必须使用域名而非纯数字 IP？
URnetwork 中继节点的 SSL/TLS 证书绑定在通配符域名（`*.fireside.bringyour.com` / `*.crisp.bringyour.com`）上。若直接使用纯 IP 连接，客户端会报证书主机名不匹配错误（Hostname Mismatch），因此必须保留官方原生域名。

### Q4：提示 "Client limit exceeded" 是什么原因？
免费版账号同时在线的并发客户端限制为 2 个。如果在短时间内反复生成了多次代理，执行 `python3 main.py --clean` 即可一键注销旧的客户端并恢复配额。

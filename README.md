# URnetwork-Proxy 住宅代理管理器

基于 [URnetwork (ur.io)](https://ur.io) 去中心化住宅级隐私网络的 Python 管理工具。支持实时拉取全球 90+ 个国家的住宅节点分布，快速生成标准格式的 HTTP、HTTPS 与 SOCKS5 代理。

---

## 特性亮点

- **零外部依赖**：基于 Python 3.10+ 标准库实现，无需 `pip install` 任何第三方包，下载即用。
- **实时地理感知**：每次运行均可直接从 `https://ur.io` 和官方 API 实时拉取最新支持的国家、城市及在线 Provider 节点数量。
- **三大路由策略**：
  - ⚡ **全球最佳链路（Best Available）**：系统自动优选当前全球延迟最低、最稳定的住宅出口。
  - 🌍 **指定国家（Country Code）**：支持指定 ISO 两位国家代码（如 `US`、`JP`、`DE`、`GB`、`SG`、`HK` 等）。
  - 🔍 **指定具体城市（City Search）**：通过模糊搜索指定全球任意热门城市（如 `Tokyo`、`Frankfurt`、`London` 等）。
- **标准格式输出**：自动格式化为 `协议://用户名:密码@Host:端口` 标准格式，兼容各类爬虫框架及 Clash、Proxifier、Shadowrocket 等代理客户端。
- **账号配额监控与自洁**：一键查看每日 30 GiB 额度使用情况，支持一键清理历史闲置客户端释放并发槽位。

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
│   ├── client.py        # 官方 API 交互客户端（登录/配额/申请代理/清理）
│   ├── locator.py       # 实时从 ur.io 抓取国家与节点分布搜索器
│   └── checker.py       # 底层 Socket 端口连通性与延迟自检
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

### 3. 命令行静默调用模式（适合脚本与自动化）

通过 CLI 参数可直接集成到已有自动化流水线中：

```bash
# 1. 自动优选最佳节点
python3 main.py --mode best

# 2. 指定国家生成（如日本 JP、美国 US、德国 DE）
python3 main.py --mode country --target JP

# 3. 指定城市生成（如东京 Tokyo）
python3 main.py --mode city --target Tokyo

# 4. 查看从 https://ur.io 实时抓取的全球支持国家及节点数
python3 main.py --list-countries

# 5. 查看当前账号配额与剩余流量
python3 main.py --status

# 6. 一键清理历史客户端释放槽位
python3 main.py --clean
```

---

## 输出格式说明

每次成功分配代理节点后，控制台将输出以下两类格式：

```text
======================================================================
【节点分配成功 - 出口策略: 指定城市 [Tokyo, Japan] (节点数: 7)】
----------------------------------------------------------------------
【标准格式代理 (用户名:密码@Host:端口)】
HTTP 代理:   http://06GOGCGUF75KBTES...:@fireside.bringyour.com:7130
SOCKS5 代理: socks5://06GOGCGUF75KBTES...:@fireside.bringyour.com:7129
HTTPS 代理:  https://06GOGCGUF75KBTES...:@fireside.bringyour.com:7131
----------------------------------------------------------------------
【官方原生返回 URL】
原生 HTTPS:  https://06gogcguf75...fireside.bringyour.com:7131
原生 HTTP:   http://fireside.bringyour.com:7130
原生 SOCKS5: socks5h://fireside.bringyour.com:7129
----------------------------------------------------------------------
端口健康状态: HTTP [在线 (11.73 ms)] | SOCKS5 [在线 (10.29 ms)]
======================================================================
```

| 元素 | 说明 |
| :--- | :--- |
| **用户名** | 分配的专属 `auth_token` |
| **密码** | 官方规范**留空**（即冒号后无内容；若特定第三方软件要求密码必填，可填写任意字符或 `none`） |
| **Host** | 官方原生中继主机（如 `fireside.bringyour.com` 或 `crisp.bringyour.com`） |
| **HTTP 端口** | 常规 HTTP/Web 代理端口 |
| **SOCKS5 端口** | SOCKS5/UDP 套接字代理端口 |
| **HTTPS 端口** | 原生 TLS 加密代理端口（可将 Token 作为二级域名免密直连） |

---

## 客户端与代码调用示例

### 1. cURL 调用

```bash
# 使用 HTTP 代理
curl -x "http://<TOKEN>:@fireside.bringyour.com:7130" https://ur.io/ip

# 使用 SOCKS5 代理
curl --socks5-hostname "<TOKEN>:@fireside.bringyour.com:7129" https://ur.io/ip
```

### 2. Python Requests 调用

```python
import requests

proxies = {
    "http": "http://<TOKEN>:@fireside.bringyour.com:7130",
    "https": "http://<TOKEN>:@fireside.bringyour.com:7130"
}

resp = requests.get("https://ur.io/ip", proxies=proxies, timeout=10)
print(resp.json())
```

### 3. 终端环境变量

```bash
export http_proxy="http://<TOKEN>:@fireside.bringyour.com:7130"
export https_proxy="http://<TOKEN>:@fireside.bringyour.com:7130"
```

---

## 常见问题 (FAQ)

### Q1：为什么必须使用域名而非纯数字 IP？
URnetwork 中继节点的 SSL/TLS 证书绑定在通配符域名（`*.fireside.bringyour.com` / `*.crisp.bringyour.com`）上。若直接使用纯 IP 连接，客户端会报证书主机名不匹配错误（Hostname Mismatch），因此必须保留官方原生域名。

### Q2：提示 "Client limit exceeded" 是什么原因？
免费版账号同时在线的并发客户端限制为 2 个。如果在短时间内反复生成了多次代理，执行 `python3 main.py --clean` 即可一键注销旧的客户端并恢复配额。

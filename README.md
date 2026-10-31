<div align="center">

# 🌐 URnetwork-Proxy

### 去中心化住宅级隐私网络 · 智能代理管理与自动化工具

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Standard%20Library)-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-orange.svg)]()
[![Protocols](https://img.shields.io/badge/Protocols-HTTP%20%7C%20HTTPS%20%7C%20SOCKS5-red.svg)]()
[![Status](https://img.shields.io/badge/Status-Maintained%20%26%20Active-success.svg)]()

[**简体中文**](./README.md) | [**快速上手**](#-快速上手) | [**配置详解**](#-配置规范-configjson) | [**命令行参考**](#-命令行静默模式-cli) | [**第三方集成**](#-第三方客户端与工具集成)

<br/>

<p align="center">
  <b>URnetwork-Proxy</b> 是一款面向 <a href="https://ur.io">URnetwork (ur.io)</a> 去中心化住宅代理网络的专业级 Python 管理套件。<br/>
  支持多重认证登录、全球 90+ 国家及热门城市实时地理感知路由、一键生成标准 <code>HTTP</code> / <code>HTTPS</code> / <code>SOCKS5</code> 代理、端到端真实出口 IP 握手深度自检，并与官方网页控制台保持全生命周期实时同步。
</p>

</div>

---

## 📑 目录

- [💡 核心特性](#-核心特性)
- [🏗️ 系统架构与工作原理](#️-系统架构与工作原理)
- [🔑 多重认证矩阵对比](#-多重认证矩阵对比)
- [⚙️ 配置规范 (config.json)](#️-配置规范-configjson)
- [🚀 快速上手](#-快速上手)
  - [1. 克隆与初始化配置](#1-克隆与初始化配置)
  - [2. 交互式终端模式 (推荐)](#2-交互式终端模式-推荐)
  - [3. 命令行静默模式 (CLI)](#3-命令行静默模式-cli)
- [🔌 第三方客户端与工具集成](#-第三方客户端与工具集成)
  - [cURL](#1-curl-终端命令行)
  - [Python 生态 (Requests / HTTPX / aiohttp)](#2-python-请求库集成)
  - [自动化测试 (Playwright / Selenium / DrissionPage)](#3-自动化测试与无头浏览器)
  - [规则分流客户端 (Clash / sing-box)](#4-代理分流客户端配置-clash--sing-box)
- [📦 Python SDK 模块化调用](#-python-sdk-模块化调用)
- [📂 项目目录结构](#-项目目录结构)
- [❓ 常见问题与避坑指南 (FAQ)](#-常见问题与避坑指南-faq)
- [📄 开源协议与免责声明](#-开源协议与免责声明)

---

## 🖥️ 多本地网关与 Web 管理（新增）

双击 **`start_web.bat`**，或执行：

```powershell
python main.py --web --open-browser
```

- **每个网关一个自定义端口**，同时支持 HTTP、HTTPS 目标 CONNECT、SOCKS5 CONNECT。
- **多个网关分别绑定国家**；定时检测、连续失败防抖、候选验证后换线，本地地址不变。
- **Web 控制台默认端口为 8777**（可自行在 `config.json` 中配置 `"web_port"` 或通过 `--web-port <端口>` 修改）。
- **配置面板**：可视化表单 / JSONC 源码双模式，直接保存到 `config.json`；支持凭据遮罩、格式校验、未保存提示和版本冲突检查，重启后生效。
- **国家排序自由可选**：支持按节点提供者数量（从多到少、从少到多）或国家名称拼音/首字母排序。
- **与命令行一致的一键深度清理**：界面中提供“清理并重置所有代理与历史客户端”，完全清空官方网页端 Proxies 列表并重置并发连接会话。
- 国家目录改用官方 `provider-locations`，**全量展示、不限制 20/25 条**；显示来源、时间和过期状态。
- 中文控制台管理网关、国家、配额和事件；纯本地静态资源，无 CDN/npm/pip 运行依赖。
- 保留原 CLI 和账号配置；网关状态位于 `.local/`，仅本机回环监听。
- 换线恢复新连接，断开的既有会话需客户端重试；重新分配不保证获得不同 IP。

详见 [多网关使用说明](docs/gateway-guide.md)、[当前架构](docs/architecture.md)。
下方原 CLI 章节保留；国家数以实时接口返回为准，非固定数量。

---

## 💡 核心特性

- **⚡ 零外部依赖 (Zero External Dependencies)**
  - 基于 Python 3.10+ 标准库（`urllib`、`socket`、`json`、`argparse`）纯手工打造，无需执行 `pip install` 安装臃肿的三方依赖，即下即跑，极端环境下秒级启动。
- **🛡️ 五重认证矩阵 (Multi-Authentication Matrix)**
  - 完美适配不同注册途径的用户：支持官方长期 API Key (`urn_...`)、网页端 5 分钟临时授权码 (`Auth Code`)、浏览器会话凭据 (`byJwt`)、经典 24 位助记词 (`Seedphrase`) 以及账号密码认证。
  - 特别针对 **Google 快捷登录** 用户提供授权码自动换取长期 Key 与自动生成 24 位专属助记词功能。
- **🌍 动态地理感知与三大路由策略**
  - **⚡ 全球最佳链路 (Best Available)**：官方智能算法实时优选全球延迟最低、稳定性最优的活跃家庭住宅宽带出口。
  - **🏳️ 指定国家/地区 (Country Code)**：支持覆盖全球 90+ 个国家的 ISO 3166-1 alpha-2 代码（如 `US`, `JP`, `DE`, `GB`, `SG`, `HK`, `FR`, `CA` 等）。
  - **🔍 指定热门城市 (City Search)**：支持模糊搜索全球大都市（如 `Tokyo`, `Frankfurt`, `London`, `Seoul`, `Sydney` 等），未命中时平滑降级。
- **🔒 防降级标准协议规范 (RFC 1929)**
  - 针对 PySocks、Clash、sing-box、Chrome 插件在空密码时自动降级为无鉴权协议进而被官方服务器拒绝的痛点，统一规范填充标准凭证格式 `http://token:none@host:port` 与 `socks5://token:none@host:port`。
- **🧪 端到端出口 IP 深度握手自检**
  - 生成代理后，工具自动通过底层 Socket 发起真实隧道握手与 HTTP/SOCKS5 转发探测，直连反查真实的住宅公网出口 IP、归属地及网络握手延迟，所见即所得。
- **🔄 全生命周期云端抽屉同步**
  - 支持调用官方管理接口实时查看网页控制台当前活跃的代理列表（PROXIES），并支持一键清理历史僵尸连接与释放节点配额。

---

## 🏗️ 系统架构与工作原理

```mermaid
flowchart TD
    subgraph ClientLayer [调用端与集成生态]
        A1[交互式 CLI 终端]
        A2[自动化脚本 / Cron Job]
        A3[爬虫 / 浏览器自动化 Playwright / Requests]
        A4[代理客户端 Clash / sing-box]
    end

    subgraph CoreEngine [URnetwork-Proxy 核心引擎]
        B1[配置加载器 core/config.py<br/>JSONC 注释解析 / 凭证读取]
        B2[认证调度器 core/client.py<br/>API Key / Auth Code / JWT / 助记词 / 密码]
        B3[地理拓扑感知 core/locator.py<br/>实时抓取 ur.io 全球 90+ 国节点]
        B4[代理生命周期管理<br/>创建 / 列表同步 / 批量注销]
        B5[出口健康自检 core/checker.py<br/>TCP Ping + SOCKS5/HTTP 隧道握手]
    end

    subgraph CloudLayer [URnetwork 官方云端基础设施]
        C1[官方 API 网关<br/>api.bringyour.com]
        C2[去中心化调度控制平面<br/>Coordinator]
        C3[全球 90+ 国家在线住宅节点<br/>Residential Providers]
    end

    ClientLayer --> CoreEngine
    B2 <--> C1
    B3 <--> C1
    B4 <--> C1
    B4 --> C2
    C2 --> C3
    B5 -. 端到端握手测试 .-> C3
```

---

## 🔑 多重认证矩阵对比

URnetwork 支持多种用户注册路径，本项目提供了完整的认证适配器：

| 认证方式 | 对应配置项 / CLI 参数 | 凭证格式示例 | 有效期 | 适用场景与推荐度 |
| :--- | :--- | :--- | :--- | :--- |
| **官方 API Key** | `"api_key"`<br/>`--api-key` | `urn_a1b2c3d4e5f6...` | 长期有效 | 🌟 **强烈推荐（生产首选）**<br/>不受浏览器退出影响，适合脚本与服务器自动化部署。 |
| **网页授权码** | `--auth-code` | `6位数字或临时代码` | 5 分钟 (一次性) | 🌟 **推荐（Google 登录首选）**<br/>网页点击获取，脚本接收后**自动兑换为长期 API Key** 并回写到配置文件。 |
| **24 位助记词** | `"seedphrase"`<br/>`--seedphrase` | `24 个 BIP-39 英文单词` | 永久有效 | 🌟 **推荐（经典钱包账户）**<br/>官网注册时备份的助记词，每次运行自动派生会话 Token。 |
| **浏览器 JWT** | `"jwt_token"`<br/>`--jwt` | `eyJhbGciOiJIUzI1Ni...` | 临时 (数小时~数天) | ⚡ **适合临时调试**<br/>从网页 F12 抓取，过期后需重新抓取。 |
| **邮箱 + 密码** | `"email"` / `"password"`<br/>`--email` / `--password` | `user@example.com` / `pwd` | 长期 | 🔹 **仅限独立密码账户**<br/>第三方 Google/GitHub 登录用户官方未设独立密码，无法使用此方式。 |

> **💡 小贴士（针对 Google 快捷登录用户）：**
> 1. 打开 [https://ur.io](https://ur.io) 登录您的账户；
> 2. 点击右上角设备连接获取一个 5 分钟 **Auth Code**；
> 3. 运行 `python main.py --auth-code 你的授权码`，程序将自动换取长期 API Key 并写入 `config.json`，以后无需重复登录！

---

## ⚙️ 配置规范 (config.json)

项目根目录下提供了带全量注释的配置模板 `config.example.json`。
系统内置了 **JSONC 注释剥离引擎**，即使在 `config.json` 中保留 `//` 或 `/* */` 注释，也不会发生 JSON 解析报错。

### 完整配置示例 (`config.example.json`)

```jsonc
{
  // ==============================================================================
  // URnetwork-Proxy 核心配置文件示例 (JSONC 格式，支持 // 与 /* */ 注释)
  // 使用方法: 复制本文件为 config.json 并填写认证信息:
  //   cp config.example.json config.json
  // ==============================================================================

  // ------------------------------------------------------------------------------
  // 1. 认证凭据配置 (五选一，优先级: api_key > jwt_token > seedphrase > email/pwd)
  // ------------------------------------------------------------------------------

  // [首选推荐] 官方长期 API Key
  // 获取方式: 登录 https://ur.io -> 网页端设置/API -> 创建 API Key (以 urn_ 开头)
  // 或者运行 python main.py --auth-code <网页5分钟授权码> 自动换取并回写
  // 特点: 长期有效、稳定不随浏览器会话过期、支持代理生命周期管理与云端同步
  "api_key": "urn_your_api_key_here",

  // [备选方案 1] 浏览器会话 Token (byJwt)
  // 获取方式: 登录 https://ur.io -> 浏览器 F12 开发者工具 -> Application -> Cookies/LocalStorage
  // 特点: 临时有效，随网页会话登出失效，适合快速免配置调试
  "jwt_token": "",

  // [备选方案 2] 经典 24 位 BIP-39 英文助记词 (单词间以单个空格分隔)
  // 获取方式: 注册 URnetwork 时备份的助记词，或通过 python main.py --generate-seedphrase 生成
  // 注意: Google 快捷登录注册的用户可能无默认助记词，建议优先使用 api_key 或通过菜单 [8] 生成
  "seedphrase": "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11 word12 word13 word14 word15 word16 word17 word18 word19 word20 word21 word22 word23 word24",

  // [备选方案 3] 邮箱与独立密码登录
  // 适用场景: 在 URnetwork 官方平台通过邮箱与独立密码注册的用户
  // 注意: 若使用 Google 账号快捷登录，因未在官方系统设置独立密码，请改用 api_key 或 auth-code 登录
  "email": "",
  "password": "",

  // ------------------------------------------------------------------------------
  // 2. 网络与 API 服务端点配置
  // ------------------------------------------------------------------------------

  // URnetwork 官方 API 网关基础地址 (保持默认即可)
  "api_base_url": "https://api.bringyour.com",

  // URnetwork 官方主站地址 (用于实时爬取全球 90+ 国家的住宅 Provider 节点分布)
  "ur_base_url": "https://ur.io",

  // 接口请求与出口握手探测超时时间 (单位: 秒，建议 10 - 30)
  "timeout_seconds": 15,

  // ------------------------------------------------------------------------------
  // 3. 默认代理路由与出口策略配置
  // ------------------------------------------------------------------------------

  // 默认路由模式:
  // - "best": 自动选择全球延迟最低、最稳定的住宅出口 (推荐)
  // - "country": 路由至指定的国家或地区 (配合 default_country)
  // - "city": 路由至指定城市 (配合城市搜索，如 Tokyo, Frankfurt, London)
  "default_mode": "best",

  // 默认国家代码 (ISO 3166-1 alpha-2 两位大写代码，如 US, JP, DE, GB, SG, HK, FR, CA 等)
  "default_country": "US"
}
```

### 配置字段释义表

| 字段名称 | 类型 | 默认值 | 详细说明与建议 |
| :--- | :---: | :---: | :--- |
| `api_key` | `string` | `""` | 官方长期 API Key，以 `urn_` 开头。生产与脚本环境首选。 |
| `jwt_token` | `string` | `""` | 浏览器端登录后的 `byJwt` 字符串。仅作为临时调试备用。 |
| `seedphrase` | `string` | `""` | 24 个英文 BIP-39 单词，单词间用一个空格分隔。 |
| `email` | `string` | `""` | 官方账户注册邮箱（配合 `password` 使用）。 |
| `password` | `string` | `""` | 账户独立密码（非第三方 OAuth 授权密码）。 |
| `api_base_url` | `string` | `"https://api.bringyour.com"` | URnetwork 官方后端 API 接口基础地址。 |
| `ur_base_url` | `string` | `"https://ur.io"` | 官方门户网站，用于拉取实时在线住宅节点分布。 |
| `timeout_seconds` | `int` | `15` | 网络请求、HTTP/SOCKS5 隧道握手及 TCP Ping 的超时时间。 |
| `default_mode` | `string` | `"best"` | 缺省运行模式：`best` (最佳链路)、`country` (国家)、`city` (城市)。 |
| `default_country` | `string` | `"US"` | 缺省国家代码，支持 ISO 两位标准字母。 |

---

## 🚀 快速上手

### 1. 克隆与初始化配置

```bash
# 克隆仓库
git clone https://github.com/your-username/URnetwork-Proxy.git
cd URnetwork-Proxy

# 从模板创建配置文件
cp config.example.json config.json

# 编辑 config.json 填入你的认证凭据 (api_key 或 seedphrase)
vim config.json
```

> **环境检查：** 确保本机安装有 Python 3.10 或更高版本 (`python3 --version`)。**无需安装任何 pip 依赖包！**

---

### 2. 交互式终端模式 (推荐)

直接运行主脚本，程序将呈现功能齐全的交互式控制台：

```bash
python main.py
```

#### 控制台效果示意

```text
======================================================================
         URnetwork 去中心化住宅代理生成与管理工具 (ur.io)
======================================================================

当前账户: User_8f29 [认证方式: API_KEY]
----------------------------------------------------------------------
请选择操作：
 [1] ⚡ 自动选择最佳连接 (Best Available)
 [2] 🌍 快速选择热门国家 (US, JP, DE, GB, SG, HK...)
 [3] 📋 实时从 https://ur.io 拉取完整国家列表并选择
 [4] 🔍 搜索具体城市/地区 (如 Tokyo, Frankfurt, London...)
 [5] 📊 查看当前账号配额与使用量
 [6] 📑 查看当前网页端活跃代理列表 (PROXIES)
 [7] 🧹 清理并重置所有代理与历史客户端 (完全清空网页 Proxies)
 [8] 🔑 账号凭证与密钥管理 (API Key / 助记词 / 网页授权)
 [0] 退出
----------------------------------------------------------------------
请输入选项 [0-8]: 1

[*] 正在申请全球最佳链路节点...

======================================================================
【节点分配成功 - 出口策略: 全球最佳链路 (自动优选)】
  网页标识名称: resident proxy · Best Available (Client ID: c_98af...)
----------------------------------------------------------------------
【标准格式代理 (用户名:密码@Host:端口)】
  HTTP 代理:   http://tok_xxxxxx:none@node3.bringyour.com:18080
  SOCKS5 代理: socks5://tok_xxxxxx:none@node3.bringyour.com:11080
  HTTPS 代理:  https://tok_xxxxxx:none@node3.bringyour.com:18443
  * 提示: 密码统一填充为 'none'，避免客户端因空密码降级导致鉴权被拒。
----------------------------------------------------------------------
【即用型测试命令 (可直接在终端中复制运行)】
  cURL (HTTP):     curl -s -x "http://tok_xxxxxx:none@node3.bringyour.com:18080" https://api.ipify.org
  cURL (SOCKS5):   curl -s --socks5-hostname "tok_xxxxxx:none@node3.bringyour.com:11080" https://api.ipify.org
----------------------------------------------------------------------
【连通性与真实出口自检】
  * TCP 端口连通性: 正常 (响应延迟: 38ms)
  * SOCKS5 握手测试: 成功！
    - 真实出口住宅 IP: 142.154.xxx.xxx
    - 代理握手总耗时: 215ms
======================================================================
```

---

### 3. 命令行静默模式 (CLI)

适合在自动化脚本、CI/CD 流水线、无头服务或 Crontab 任务中调用：

| 目标场景 | 命令行示例 | 说明 |
| :--- | :--- | :--- |
| **申请最佳链路代理** | `python main.py --mode best` | 自动选取全球最佳住宅节点并自检 |
| **申请指定国家代理** | `python main.py --mode country --target JP` | 申请日本住宅节点（支持 US/DE/GB 等） |
| **申请指定城市代理** | `python main.py --mode city --target Tokyo` | 搜索并申请指定城市住宅节点 |
| **跳过握手检测 (极速分配)** | `python main.py --mode best --no-check` | 不执行端到端检测，毫秒级输出节点串 |
| **网页 Auth Code 换 Key** | `python main.py --auth-code 849201` | 网页临时码自动换取长期 API Key 并写入配置 |
| **生成专属助记词** | `python main.py --generate-seedphrase` | 为当前账号生成 24 位专属助记词 |
| **创建新 API Key** | `python main.py --create-api-key MyServerKey` | 为账号生成具名长期 API Key |
| **查看账户配额状态** | `python main.py --status` | 输出当前网络名、认证方式、剩余流量与使用量 |
| **列出网页端活跃代理** | `python main.py --list-proxies` | 获取官网抽屉中所有已分配的客户端实例 |
| **一键重置并清理代理** | `python main.py --clean` | 批量删除官网 Proxies 抽屉所有实例并重置并发 |
| **查询全球在线国家列表** | `python main.py --list-countries` | 实时爬取官网 90+ 国家在线 Provider 数 |

---

## 🔌 第三方客户端与工具集成

URnetwork 底层为每一次代理申请**同时开放三个标准端口**：
- `http_port` (HTTP 隧道代理)
- `https_port` (HTTPS 隧道代理)
- `socks_port` (SOCKS5 代理)

以下为常见开发语言、自动化测试工具及规则分流客户端的集成示例（以分配得到的节点 `node3.bringyour.com`, Token `tok_abc123` 为例）：

### 1. cURL 终端命令行

```bash
# 通过 HTTP 代理请求目标
curl -s -x "http://tok_abc123:none@node3.bringyour.com:18080" https://api.ipify.org

# 通过 SOCKS5 代理请求目标 (由代理端远程解析 DNS，杜绝本地 DNS 污染)
curl -s --socks5-hostname "tok_abc123:none@node3.bringyour.com:11080" https://api.ipify.org
```

---

### 2. Python 请求库集成

#### Requests

```python
import requests

proxies = {
    "http": "http://tok_abc123:none@node3.bringyour.com:18080",
    "https": "http://tok_abc123:none@node3.bringyour.com:18080"
}

resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=15)
print("出口 IP:", resp.json()["origin"])
```

#### HTTPX (支持 HTTP/2 与 Async 异步)

```python
import httpx
import asyncio

async def test_proxy():
    proxy_url = "http://tok_abc123:none@node3.bringyour.com:18080"
    async with httpx.AsyncClient(proxy=proxy_url, timeout=15) as client:
        resp = await client.get("https://httpbin.org/ip")
        print("HTTPX 出口 IP:", resp.json()["origin"])

asyncio.run(test_proxy())
```

#### aiohttp (SOCKS5 异步协程)

```python
import aiohttp
import asyncio
from aiohttp_socks import ProxyConnector

async def test_aiohttp():
    connector = ProxyConnector.from_url("socks5://tok_abc123:none@node3.bringyour.com:11080")
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get("https://api.ipify.org?format=json") as resp:
            data = await resp.json()
            print("aiohttp 出口 IP:", data["ip"])

asyncio.run(test_aiohttp())
```

---

### 3. 自动化测试与无头浏览器

#### Playwright (Python)

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        proxy={
            "server": "socks5://node3.bringyour.com:11080",
            "username": "tok_abc123",
            "password": "none"
        }
    )
    page = browser.new_page()
    page.goto("https://ipinfo.io/json")
    print("浏览器探测住宅 IP:", page.inner_text("pre"))
    browser.close()
```

#### DrissionPage

```python
from DrissionPage import ChromiumPage, ChromiumOptions

co = ChromiumOptions()
co.set_proxy("socks5://tok_abc123:none@node3.bringyour.com:11080")
page = ChromiumPage(addr_or_opts=co)
page.get("https://api.ipify.org")
print("DrissionPage IP:", page.html)
page.quit()
```

---

### 4. 代理分流客户端配置 (Clash / sing-box)

#### Clash / Mihomo (Clash.Meta) YAML 配置片段

```yaml
proxies:
  # SOCKS5 节点定义
  - name: "URnetwork-JP-SOCKS5"
    type: socks5
    server: node3.bringyour.com
    port: 11080
    username: tok_abc123
    password: none
    udp: false

  # HTTP 节点定义
  - name: "URnetwork-JP-HTTP"
    type: http
    server: node3.bringyour.com
    port: 18080
    username: tok_abc123
    password: none
```

#### sing-box Outbounds JSON 配置片段

```json
{
  "outbounds": [
    {
      "type": "socks",
      "tag": "urnetwork-out",
      "server": "node3.bringyour.com",
      "server_port": 11080,
      "version": "5",
      "username": "tok_abc123",
      "password": "none"
    }
  ]
}
```

---

## 📦 Python SDK 模块化调用

除了通过命令行或终端交互，你还可以将 `URClient` 作为 Python 模块直接集成到你的爬虫框架、代理池调度器或微服务中：

```python
from core.client import URClient

# 1. 初始化客户端 (支持传入 api_key 或 seedphrase)
client = URClient(
    api_key="urn_your_api_key_here",
    timeout=15
)

# 2. 验证登录并获取网络信息
info = client.login()
print(f"登录成功: 网络 [{info['network_name']}] | 用户ID: {info['user_id']}")

# 3. 查看流量配额
status = client.get_account_status()
print(f"今日配额: {status['daily_quota_gib']} GiB | 剩余: {status['remaining_gib']} GiB")

# 4. 动态申请指定国家住宅代理节点 (如日本 JP)
proxy = client.create_proxy(mode="country", target="JP")

print(f"分配成功: {proxy['host']}")
print(f"SOCKS5 代理串: {proxy['standard_socks5']}")
print(f"HTTP 代理串:   {proxy['standard_http']}")

# 5. 使用完毕后，精准清理该代理实例 (释放云端连接数)
client.remove_proxy(proxy["client_id"])
print("已成功释放该住宅节点实例！")
```

---

## 📂 项目目录结构

```text
URnetwork-Proxy/
├── config.json              # 核心配置文件 (用户实际凭据，自动由 load_config 管理)
├── config.example.json      # 带详尽注释的配置模板 (JSONC 格式规范)
├── main.py                  # CLI 命令行入口与交互式控制台主逻辑
├── requirements.txt         # 依赖说明清单 (0 外部依赖，原生标准库运行)
├── start.bat                # Windows 快速启动批处理脚本
├── core/                    # 核心业务逻辑模块
│   ├── __init__.py
│   ├── config.py            # 配置加载器：支持 JSONC 注释解析、尾部逗号清洗与自动保存
│   ├── client.py            # 官方 API 网关客户端 (多重鉴权/配额/节点申请/云端抽屉管理)
│   ├── locator.py           # 实时网络拓扑抓取器 (从 ur.io 抓取国家与节点分布、城市模糊检索)
│   └── checker.py           # 端到端检测引擎 (TCP Ping 延时探测 + SOCKS5/HTTP 出口公网 IP 反查)
└── README.md                # 项目全量技术说明文档
```

---

## ❓ 常见问题与避坑指南 (FAQ)

### Q1: 为什么代理用户名密码中，密码固定填入 `none`？
> **答：** URnetwork 底层鉴权由 `auth_token` 唯一定位。根据 RFC 1929 规范，很多客户端（如 Python `PySocks`、`urllib`、部分版本的 Clash 及浏览器插件）如果检测到密码为空字符串 `""`，会**主动将鉴权方式降级为 0x00 (No Authentication)**，导致发送的握手包不包含用户名，进而被官方网关直接断开连接。我们在生成标准代理串时规范填入 `none`，既确保客户端严格发送 RFC 1929 鉴权子协商，又兼顾了各大软件的兼容性。

### Q2: 我是通过 Google 邮箱快捷登录注册的，没有 24 位助记词，该如何使用？
> **答：**
> 1. 打开 [https://ur.io](https://ur.io) 登录，在界面中点击设备连接，获取一个 5 分钟有效的 `Auth Code`（授权码）；
> 2. 运行 `python main.py --auth-code 你的授权码`，程序会自动用此授权码换取一个持久有效的官方长期 API Key，并自动保存进 `config.json`；
> 3. 若您想要备份专属助记词，也可以在交互菜单中选择 `[8] 凭证管理 -> [3] 生成 24 位专属助记词`，系统会为您的账号自动派生出助记词。

### Q3: 为什么有时搜索特定城市会提示“未找到可用节点，自动降级为最佳连接”？
> **答：** 去中心化住宅网络的节点是由全球家庭宽带 Provider 提供的，某些小众城市在特定时间段内可能暂时没有 Provider 在线。为了保障业务不中断，工具会智能捕获未命中状态，并自动降级为系统全球优选链路（Best Available）。

### Q4: 网页端控制台的 PROXIES 列表中堆积了很多历史节点，如何清理？
> **答：** 可以直接在交互菜单中选择 `[7] 清理并重置所有代理与历史客户端`，或者直接在命令行执行 `python main.py --clean`。程序会调用 `/network/remove-client` 逐个向官方注销代理实例，彻底清空网页端列表，并重置客户端并发计数。

### Q5: 为什么端到端握手检测通过，但我使用某个网站时依然连接缓慢？
> **答：** 住宅代理的物理链路为：`你的主机 -> URnetwork 调度网关 -> 分布在全球各地的真实家庭宽带出口 -> 目标网站`。端到端握手耗时反映了整个代理链路的综合往返延迟。如果目标服务器在异地跨洲，建议优先使用 `--mode country` 指定与目标服务同区域的国家节点以获得最低延迟。

---

## 📄 开源协议与免责声明

- 本项目采用 [MIT 许可证](LICENSE) 开源。
- **免责声明：** 本项目仅作为与 URnetwork (ur.io) 官方 API 进行交互的客户端辅助管理工具，所有网络节点与住宅出口均由 URnetwork 官方及网络提供者提供。使用者在使用本工具时应严格遵守当地法律法规以及服务商的服务条款，严禁用于任何非法入侵、网络攻击或侵犯隐私等违法活动。开发者不对因使用本工具造成的任何直接或间接后果承担法律责任。

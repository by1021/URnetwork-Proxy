<div align="center">

# 🌐 URnetwork-Proxy

### 去中心化住宅级隐私网络 · 智能代理调度、多本地网关与 Web 管理套件

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Standard%20Library)-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-orange.svg)]()
[![Protocols](https://img.shields.io/badge/Protocols-HTTP%20%7C%20HTTPS%20%7C%20SOCKS5-red.svg)]()
[![Tests](https://img.shields.io/badge/Tests-98%20Passed-success.svg)]()

[**简体中文**](./README.md) | [**快速上手**](#-快速上手与多端启动) | [**Web 控制台**](#️-web-管理控制台与双模式配置面板) | [**配置规范**](#️-配置规范-configjson) | [**命令行参考**](#-命令行静默模式-cli) | [**第三方集成**](#-第三方客户端与系统集成)

<br/>

<p align="center">
  <b>URnetwork-Proxy</b> 是一款面向 <a href="https://ur.io">URnetwork (ur.io)</a> 去中心化住宅代理网络的专业级 Python 管理套件。<br/>
  集成<b>单端口 HTTP/HTTPS/SOCKS5 混合协议复用</b>、<b>多本地网关生命周期管理与故障自愈</b>、<b>全球 90+ 国家实时拓扑感知</b>、<b>现代化 Web 控制台与双模式配置中心</b>，运行核心使用 Python 标准库，HTTPS 网关需提供证书或通过 OpenSSL 生成。
</p>

</div>

---

## 📑 目录

- [💡 核心特性](#-核心特性)
- [🏗️ 系统全景架构](#️-系统全景架构)
- [🚀 快速上手与多端启动](#-快速上手与多端启动)
  - [1. 克隆与初始化配置](#1-克隆与初始化配置)
  - [2. Linux / macOS 运行](#2-linux--macos-运行)
  - [3. Windows 运行](#3-windows-运行)
  - [4. Linux 生产环境后台常驻与 Systemd 守护进程](#4-linux-生产环境后台常驻与-systemd-守护进程)
- [🖥️ Web 管理控制台与双模式配置面板](#️-web-管理控制台与双模式配置面板)
  - [本地网关与动态调度](#1-本地多网关管理与健康自愈)
  - [全球 90+ 国家实时拓扑](#2-全球-90-国家实时拓扑)
  - [双模式配置中心 (可视化表单 + JSONC 源码)](#3-双模式配置中心-form--jsonc)
  - [安全机制与凭据保护](#4-安全机制与存储保护)
- [⚙️ 配置规范 (config.json)](#️-配置规范-configjson)
  - [完整配置示例](#完整配置示例-configexamplejson)
  - [配置字段释义表](#配置字段释义表)
- [🔑 五重认证矩阵对比与最佳实践](#-五重认证矩阵对比与最佳实践)
- [💻 命令行静默模式 (CLI)](#-命令行静默模式-cli)
- [🔌 第三方客户端与系统集成](#-第三方客户端与系统集成)
  - [cURL 终端命令行](#1-curl-终端命令行)
  - [Python 生态 (Requests / HTTPX / aiohttp)](#2-python-生态集成)
  - [自动化测试 (Playwright / DrissionPage)](#3-自动化测试与无头浏览器)
  - [规则分流客户端 (Clash / sing-box)](#4-代理分流客户端配置-clash--sing-box)
- [📦 Python SDK 编程式调用](#-python-sdk-编程式调用)
- [📂 完整项目目录结构](#-完整项目目录结构)
- [🧪 自动化测试与质量验证](#-自动化测试与质量验证)
- [❓ 常见问题与避坑指南 (FAQ)](#-常见问题与避坑指南-faq)
- [📄 开源协议与免责声明](#-开源协议与免责声明)

---

## 💡 核心特性

- **⚡ 零外部依赖 (Zero External Dependencies)**
  - 基于 Python 3.10+ 标准库（`asyncio`、`urllib`、`socket`、`http.server`、`json`、`argparse`）纯手工构建；核心无需 pip 依赖；HTTPS 证书生成需要系统 OpenSSL 或可选的 cryptography。
  - 前端控制台采用纯原生 HTML5/ES6/CSS3 构建，无 Node.js、npm 或外部 CDN 网络依赖，完全离线可靠运行。
- **🔀 单端口协议全复用 (Single-Port Mixed Multiplexing)**
  - 独创单端口透明协议嗅探机制：首字节自动识别 SOCKS5（`0x05`）、明文 HTTP（ASCII 字符）与 HTTPS 代理 TLS 握手（`0x16`），**同一本地端口同时处理 HTTPS 加密代理、HTTP 明文代理与 SOCKS5 代理连接**，无需为不同协议开放多组端口。
- **🛡️ 多本地网关与智能自愈 (Multi-Gateway Engine & Auto-Healing)**
  - 支持创建多个相互独立的本地监听网关（如 `10801` 绑定日本、`10802` 绑定美国），客户端只需连接固定本地端口。
  - **故障自愈与平滑无缝换线**：后台定时多点探针检测，触发失败阈值后自动在云端申请并严格预检新候选链路；验证成功后平滑原子切换，老连接享受 30 秒排空保护，杜绝长连接突发雪崩。
- **🖥️ 现代化 Web 控制台与双模式配置中心**
  - 提供响应式 Web 管理界面，支持可视化添加、编辑、启动、停止与手动重连网关。
  - **双模式配置面板**：可视化表单与带语法高亮的 JSONC 源码编辑自由切换；敏感凭据自动脱敏遮罩；具备未保存脏检查、字段级合法性验证与外部并发冲突检测机制。
- **🌍 动态地理感知与全量拓扑**
  - 直接对接官方 `/network/provider-locations` 生产接口，实时呈现全球 90+ 国家在线住宅节点分布；提供节点数降序、升序及国家拼音/首字母自由排序。
- **🔑 五重认证矩阵 (Multi-Authentication Matrix)**
  - 全面覆盖各种注册路径：官方长期 API Key (`urn_...`)、网页 5 分钟临时授权码 (`Auth Code`)、浏览器会话凭据 (`byJwt`)、24 位助记词 (`Seedphrase`) 以及账号密码认证。针对 Google 快捷登录用户提供一键兑换长期 Key 机制。
- **🔒 防降级标准协议规范 (RFC 1929)**
  - 统一规范填充标准凭据格式（密码固定填充为 `none`），彻底根治各类客户端（PySocks、Clash、sing-box、Chrome 插件）在空密码时自动降级为无认证协议进而被远端服务器拒绝的痛点。
- **🧪 端到端出口深度自检**
  - 底层 Socket 发起真实隧道握手与双探针检测，穿透验证真实公网出口住宅 IP、归属地理位置与端到端往返延迟（RTT）。
- **🔄 全生命周期云端抽屉同步**
  - 一键列出与清空官方网页端 Proxies 抽屉实例，彻底清理历史僵尸连接并释放并发连接配额。

---

## 🏗️ 系统全景架构

```mermaid
flowchart TD
    subgraph ClientLayer [客户端应用与调用生态]
        C1[命令行工具 / cURL]
        C2[网络爬虫 / Requests / HTTPX / aiohttp]
        C3[浏览器自动化 / Playwright / DrissionPage]
        C4[代理客户端 / Clash / sing-box]
    end

    subgraph GatewayLayer [本地多网关与协议复用层 (core/multiplexer.py)]
        G1[本地监听端口 10801<br/>HTTP / HTTPS / SOCKS5 混合]
        G2[本地监听端口 10802<br/>HTTP / HTTPS / SOCKS5 混合]
        MUX{协议嗅探器<br/>First-Byte Sniffer}
        MUX -->|0x05| SOCKS[SOCKS5 CONNECT 处理器]
        MUX -->|ASCII| HTTP[HTTP / HTTPS CONNECT 处理器]
    end

    subgraph EngineLayer [网关调度与自愈引擎 (core/gateway.py)]
        GE[GatewayEngine 调度中心]
        PROBE[双探针出口健康检查<br/>core/checker.py]
        HEAL[故障防抖与平滑换线控制器]
        STORE[(状态原子持久化 .local/<br/>core/storage.py · OS文件锁)]
        GE <--> PROBE
        GE <--> HEAL
        GE <--> STORE
    end

    subgraph WebLayer [本地 Web 管理服务 (core/web_server.py & web/)]
        WS[ThreadingHTTPServer 默认 8777]
        API[RESTful 管理 API + Token/Cookie 鉴权]
        CFG_ED[双模式配置中心<br/>core/config_editor.py]
        UI[现代 Web 单页应用 HTML5/ES6/CSS3]
        WS --- API
        WS --- CFG_ED
        WS --- UI
    end

    subgraph CoreServices [核心服务与配置加载]
        CFG[配置加载器 core/config.py<br/>JSONC 注释剥离 / 原子写盘]
        CLI_MNG[官方 API 客户端 core/client.py<br/>五重鉴权 / 配额 / 云端抽屉注销]
        LOC[拓扑抓取器 core/locator.py<br/>90+ 国家在线住宅节点]
    end

    subgraph CloudLayer [URnetwork 官方云端基础设施 (ur.io)]
        CLOUD_API[官方 API 网关<br/>api.bringyour.com]
        COORD[去中心化调度控制平面<br/>Coordinator]
        NODES[全球 90+ 国家活跃住宅宽带节点<br/>Residential Providers]
    end

    ClientLayer --> G1
    ClientLayer --> G2
    G1 & G2 --> MUX
    SOCKS & HTTP --> GE
    GE --> CLI_MNG
    UI <--> API
    API <--> GE
    CLI_MNG <--> CLOUD_API
    LOC <--> CLOUD_API
    CLOUD_API --> COORD --> NODES
    PROBE -. 双向连通性探测 .-> NODES
```

---

## 🚀 快速上手与多端启动

### 1. 克隆与初始化配置

```bash
# 1. 克隆代码仓库
git clone https://github.com/your-username/URnetwork-Proxy.git
cd URnetwork-Proxy

# 2. 从配置模板复制实际配置文件
cp config.example.json config.json

# 3. 编辑配置文件填入你的认证凭据 (推荐填入 api_key)
vim config.json  # 或使用 nano、VS Code 打开编辑
```

> **环境检查：** Python 3.10+；HTTPS 网关需提供 PEM 证书/私钥，或使用系统 OpenSSL 首次生成（无 OpenSSL 时可选安装 cryptography）。证书配置和客户端信任方法见 [网关使用指南](docs/gateway-guide.md)。

---

### 2. Linux / macOS 运行

本项目已为 Linux / macOS 提供完整的可执行脚本（已默认包含执行权限）：

#### 启动 Web 管理控制台 (推荐)
```bash
./start_web.sh
```
- **智能运行适配**：若处于桌面环境（X11 / Wayland），脚本将自动调用默认浏览器打开管理界面；若处于无图形界面或 SSH 远程服务器，将抑制浏览器错误并清晰输出访问地址：
  ```text
  管理访问地址：http://127.0.0.1:8777/
  登录方式：用户名 / 密码
  HTTP、HTTPS 与 SOCKS5 共用每个网关的自定义端口。Ctrl+C 停止。
  ```
- **管理账号**：默认用户名 `admin`、密码 `admin`；登录后在配置面板修改 `web_username` / `web_password`，保存后重启生效。它们与上游 `email` / `password` 独立。管理模式配置读取或校验失败时会停止启动，不回退到默认账号。
- **访问地址自适应**：登录页、控制台与配置面板使用浏览器地址栏中的主机；管理入口保留实际 HTTP/HTTPS 协议与端口，适配 IPv4、IPv6、域名和端口转发。服务端路由 IP / 监听地址单独列为诊断，不再替换实际访问入口；诊断接口失败也保留当前入口。代理出口 IP 与这些地址分开显示。
- **其他设备访问管理页**：运行 `./start_web.sh --web-host 0.0.0.0`（也可在配置中设 `web_host`），并确保管理端口已放行。默认回环监听保持不变；更换管理默认密码，远程登录使用 HTTPS 或 SSH 隧道。网关表单默认双栈全接口监听，远程使用应设置代理认证并限制防火墙来源；API 指定回环地址时仍只监听回环。
- **自定义管理端口**：
  ```bash
  ./start_web.sh --web-port 8999
  ```

#### 启动交互式 CLI 终端菜单
```bash
./start.sh
```
支持直接通过交互式控制台进行一键测速、节点申请、国家拓扑拉取与云端代理清理。

---

### 3. Windows 运行

- **启动 Web 管理控制台**：双击运行根目录下的 **`start_web.bat`**。
- **启动交互式 CLI 终端**：双击运行根目录下的 **`start.bat`**。

亦可在 PowerShell 或 CMD 中运行：
```powershell
python main.py --web --open-browser
```

---

### 4. Linux 生产环境后台常驻与 Systemd 守护进程

在 Linux 服务器（Ubuntu / Debian / CentOS / RHEL / Arch）上长期部署代理网关，推荐使用 Systemd 进行后台托管：

#### 方案 A：使用 Systemd 托管服务（推荐）

项目内已内置服务单元文件 `systemd/urnetwork-proxy.service`：

```bash
# 1. 复制 service 文件至系统目录
sudo cp systemd/urnetwork-proxy.service /etc/systemd/system/

# 2. 根据实际路径与用户修改配置 (将 WorkingDirectory 指向项目根目录)
sudo vim /etc/systemd/system/urnetwork-proxy.service

# 3. 重载并启动服务
sudo systemctl daemon-reload
sudo systemctl enable --now urnetwork-proxy

# 4. 查看服务运行状态与访问链接 (包含鉴权 Token)
sudo systemctl status urnetwork-proxy
sudo journalctl -u urnetwork-proxy -n 20 --no-pager
```

#### 方案 B：使用 Nohup 或 Tmux 后台运行

```bash
# 使用 nohup 后台运行并输出日志到 web.log
nohup ./start_web.sh > web.log 2>&1 &

# 查看后台运行日志与登录令牌
tail -f web.log
```

---

## 🖥️ Web 管理控制台与双模式配置面板

### 1. 本地多网关管理与健康自愈

![控制台概览](docs/assets/dashboard.png)

- **自定义独立端口**：可以任意创建多个网关（如 `10801`, `10802`, `10803`），分别指定不同出口国家或全球最佳链路。
- **单端口全协议接入**：
  ```text
  HTTP 代理地址:   http://127.0.0.1:10801
  SOCKS5 代理地址: socks5://127.0.0.1:10801
  ```
- **自动监测与平滑换线**：
  - 默认每 30 秒执行一次双探针健康检查（验证代理鉴权、TLS 握手证书与出口 IP 反查）。
  - 连续失败达到阈值（默认 3 次）自动触发候选链路申请与全量验证；候选成功后原子切换，旧链路维持 30 秒排空期，老连接不受突发中断影响。
  - 单网关限频防抖（每小时最多申请 12 次，退避间隔 5~300 秒），防止网络波动造成频繁刷取。

---

### 2. 全球 90+ 国家实时拓扑

- 基于官方 `/network/provider-locations` 生产接口，全量呈现当前在线住宅节点分布，**不做任何数量截断**。
- 支持按国家提供者节点数（从多到少、从少到多）或按国家英文字母排序，支持搜索国家代码、英文名与中文名。

---

### 3. 双模式配置中心 (Form + JSONC)

点击控制台左侧 **配置面板**，支持实时管理系统配置并落盘写入 `config.json`：

| 功能模式 | 界面预览 | 特性与优势 |
| :---: | :---: | :--- |
| **可视化表单** | ![表单模式](docs/assets/config-panel.png) | • 友好分组管理认证方式、网关服务地址、超时时间、出口模式与控制台端口<br/>• 敏感凭据自动以 `__KEEP_EXISTING__` 占位符脱敏展示，点击小眼睛图标可查看或输入新值覆盖 |
| **JSONC 源码编辑** | ![源码模式](docs/assets/config-panel-source.png) | • 深度支持带 `//` 与 `/* */` 注释的 JSONC 语法高亮与语法校验<br/>• 实时格式化并保留未知扩展字段，支持直接微调底层高级参数 |

---

### 4. 安全机制与存储保护

- **单实例进程锁**：基于操作系统原生文件锁（POSIX `fcntl.flock` / Windows `msvcrt.locking`），确保同一数据目录绝不允许多个进程并发冲突。
- **配置防冲突机制**：保存配置前自动校验文件哈希；若其他终端或编辑器修改了 `config.json`，控制台将主动阻断保存并提示版本冲突。
- **会话令牌隔离**：启动终端生成的带 Token 链接仅供本机首次握手；浏览器载入后自动抹除 URL 片段并将 Token 写入隔离会话，杜绝凭据外泄。

---

## ⚙️ 配置规范 (config.json)

项目根目录下的 `config.json` 采用 JSONC 标准，支持保留注释。

### 完整配置示例 (`config.example.json`)

```jsonc
{
  // ==============================================================================
  // URnetwork-Proxy 核心配置文件示例 (JSONC 格式，支持 // 与 /* */ 注释)
  // 使用方法: 复制本文件为 config.json 并填写认证信息:
  //   cp config.example.json config.json
  // ==============================================================================

  // ------------------------------------------------------------------------------
  // 1. 认证凭据配置 (五选一，优先级: api_key > auth_code > jwt_token > seedphrase > email/pwd)
  // ------------------------------------------------------------------------------

  // [首选推荐] 官方长期 API Key
  // 获取方式: 登录 https://ur.io -> 网页端设置/API -> 创建 API Key (以 urn_ 开头)
  // 或者运行 python main.py --auth-code <网页5分钟授权码> 自动换取并回写
  "api_key": "urn_your_api_key_here",

  // [备选方案 1] 浏览器会话 Token (byJwt)
  "jwt_token": "",

  // [备选方案 2] 经典 24 位 BIP-39 英文助记词 (单词间以单个空格分隔)
  "seedphrase": "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11 word12 word13 word14 word15 word16 word17 word18 word19 word20 word21 word22 word23 word24",

  // [备选方案 3] 邮箱与独立密码登录
  "email": "",
  "password": "",

  // ------------------------------------------------------------------------------
  // 2. 网络与 API 服务端点配置
  // ------------------------------------------------------------------------------

  // URnetwork 官方 API 网关基础地址 (通常保持默认即可)
  "api_base_url": "https://api.bringyour.com",

  // URnetwork 官方主站地址 (用于实时抓取全球 90+ 国家的住宅 Provider 节点分布)
  "ur_base_url": "https://ur.io",

  // 接口请求与出口握手探测超时时间 (单位: 秒，建议 10 - 30)
  "timeout_seconds": 15,

  // ------------------------------------------------------------------------------
  // 3. 默认代理路由与出口策略配置
  // ------------------------------------------------------------------------------

  // 默认路由模式: "best" (自动优选) | "country" (指定国家) | "city" (指定城市)
  "default_mode": "best",

  // 默认国家代码 (ISO 两位大写代码，如 US, JP, DE, GB, SG, HK, FR, CA 等)
  "default_country": "US",

  // ------------------------------------------------------------------------------
  // 4. Web 控制台配置
  // ------------------------------------------------------------------------------

  // 本地多网关 Web 管理控制台端口 (默认 8777，可自行修改)
  "web_port": 8777,
  "web_username": "admin",
  "web_password": "admin"
}
```

### 配置字段释义表

| 字段名称 | 类型 | 默认值 | 详细说明与建议 |
| :--- | :---: | :---: | :--- |
| `api_key` | `string` | `""` | 官方长期 API Key，以 `urn_` 开头。生产与脚本环境**强烈推荐**。 |
| `jwt_token` | `string` | `""` | 浏览器端登录导出的 `byJwt` 字符串。临时调试备用。 |
| `seedphrase` | `string` | `""` | 24 个英文 BIP-39 助记词，单词间以单空格分隔。 |
| `email` | `string` | `""` | 官方注册邮箱（配合 `password` 使用）。 |
| `password` | `string` | `""` | 官方账号独立登录密码。 |
| `api_base_url` | `string` | `"https://api.bringyour.com"` | 官方后端 API 接口基础地址。 |
| `ur_base_url` | `string` | `"https://ur.io"` | 官方门户网站地址，用于拉取住宅节点拓扑。 |
| `timeout_seconds` | `int` | `15` | 网络请求、底层握手自检及 TCP Ping 的超时时间（秒）。 |
| `default_mode` | `string` | `"best"` | 缺省运行模式：`best` (最佳链路)、`country` (国家)、`city` (城市)。 |
| `default_country` | `string` | `"US"` | 缺省国家代码，支持 ISO 3166-1 两位标准大写代码。 |
| `web_port` | `int` | `8777` | Web 管理控制台本地监听端口。 |
| `web_username` | `string` | `"admin"` | 控制台管理用户名；修改后重启生效。 |
| `web_password` | `string` | `"admin"` | 控制台管理密码；配置面板读取时遮罩，修改后重启生效。 |

---

## 🔑 五重认证矩阵对比与最佳实践

| 认证方式 | 对应配置项 / CLI 参数 | 凭证格式示例 | 有效期 | 适用场景与推荐度 |
| :--- | :--- | :--- | :--- | :--- |
| **官方 API Key** | `"api_key"`<br/>`--api-key` | `urn_a1b2c3d4e5f6...` | 长期有效 | 🌟 **强烈推荐（生产首选）**<br/>稳定性最高，不受网页登出影响，适合服务器后台长期运行。 |
| **网页授权码** | `--auth-code` | `6位数字或临时代码` | 5 分钟 (一次性) | 🌟 **推荐（Google 登录首选）**<br/>网页点击获取，脚本接收后**自动兑换为长期 API Key** 并回写到配置文件。 |
| **24 位助记词** | `"seedphrase"`<br/>`--seedphrase` | `24 个 BIP-39 英文单词` | 永久有效 | 🌟 **推荐（经典钱包账户）**<br/>官网注册时备份的助记词，每次运行自动派生安全会话 Token。 |
| **浏览器 JWT** | `"jwt_token"`<br/>`--jwt` | `eyJhbGciOiJIUzI1Ni...` | 临时 (数小时~数天) | ⚡ **适合临时调试**<br/>从网页 F12 抓取，随浏览器登录状态失效。 |
| **邮箱 + 密码** | `"email"` / `"password"`<br/>`--email` / `--password` | `user@domain.com` / `pwd` | 长期 | 🔹 **仅限独立密码账户**<br/>第三方 Google/GitHub 快捷登录用户因未设密码无法使用此方式。 |

> **💡 小贴士（针对 Google 快捷登录用户）：**
> 1. 打开 [https://ur.io](https://ur.io) 登录您的账户；
> 2. 点击页面设备连接获取一个 5 分钟 **Auth Code**；
> 3. 在终端中运行：
>    ```bash
>    ./start.sh --auth-code 你的授权码
>    ```
>    程序将自动换取长期 API Key 并写入 `config.json`，从此告别重复登录！

---

## 💻 命令行静默模式 (CLI)

适合在自动化脚本、CI/CD 流水线、无头服务或 Crontab 定时任务中调用：

| 目标场景 | Linux / macOS 指令 | Windows 指令 |
| :--- | :--- | :--- |
| **启动 Web 管理控制台** | `./start_web.sh` | `start_web.bat` |
| **指定控制台端口启动** | `./start_web.sh --web-port 8999` | `python main.py --web --web-port 8999` |
| **申请全球最佳链路节点** | `./start.sh --mode best` | `python main.py --mode best` |
| **申请指定国家代理 (如日本)**| `./start.sh --mode country --target JP` | `python main.py --mode country --target JP` |
| **申请指定城市代理 (如东京)**| `./start.sh --mode city --target Tokyo` | `python main.py --mode city --target Tokyo` |
| **毫秒级极速分配 (跳过自检)**| `./start.sh --mode best --no-check` | `python main.py --mode best --no-check` |
| **网页 Auth Code 自动换 Key** | `./start.sh --auth-code 849201` | `python main.py --auth-code 849201` |
| **为当前账号生成助记词** | `./start.sh --generate-seedphrase` | `python main.py --generate-seedphrase` |
| **为当前账号创建新 API Key** | `./start.sh --create-api-key MyServer` | `python main.py --create-api-key MyServer` |
| **查看账户流量与配额状态** | `./start.sh --status` | `python main.py --status` |
| **查询网页端当前活跃 Proxies**| `./start.sh --list-proxies` | `python main.py --list-proxies` |
| **一键清空官网 Proxies 抽屉** | `./start.sh --clean` | `python main.py --clean` |
| **查询全球 90+ 在线国家列表** | `./start.sh --list-countries` | `python main.py --list-countries` |

---

## 🔌 第三方客户端与系统集成

URnetwork-Proxy 支持**两种接入模式**：
1. **本地多网关模式（推荐）**：客户端直连本机网关（如 `127.0.0.1:10801`），享受单端口 HTTP/HTTPS/SOCKS5 自动识别与后台断线自动换线自愈。
2. **云端直连模式**：通过 CLI 生成的标准代理串直接接入官方远端出口。

以下演示接入本地网关（监听于 `127.0.0.1:10801`）与云端直连节点的调用方法：

### 1. cURL 终端命令行

```bash
# 模式 A: 接入本地自愈网关 (无需账号密码，同一个端口支持 HTTP 与 SOCKS5)
curl -s -x "http://127.0.0.1:10801" https://api.ipify.org
curl -s --socks5-hostname "127.0.0.1:10801" https://api.ipify.org

# 模式 B: 直连云端住宅节点 (由 CLI 模式分配输出)
curl -s -x "http://tok_abc123:none@node3.bringyour.com:18080" https://api.ipify.org
curl -s --socks5-hostname "tok_abc123:none@node3.bringyour.com:11080" https://api.ipify.org
```

---

### 2. Python 生态集成

#### Requests

```python
import requests

# 接入本地自愈网关 (127.0.0.1:10801)
proxies = {
    "http": "http://127.0.0.1:10801",
    "https": "http://127.0.0.1:10801",
}

resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=15)
print("当前住宅出口 IP:", resp.json()["origin"])
```

#### HTTPX (支持 HTTP/2 与 Async 异步)

```python
import asyncio
import httpx

async def main():
    async with httpx.AsyncClient(proxy="http://127.0.0.1:10801", timeout=15) as client:
        resp = await client.get("https://httpbin.org/ip")
        print("HTTPX 出口 IP:", resp.json()["origin"])

asyncio.run(main())
```

#### aiohttp (SOCKS5 异步协程)

```python
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector

async def main():
    connector = ProxyConnector.from_url("socks5://127.0.0.1:10801")
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get("https://api.ipify.org?format=json") as resp:
            data = await resp.json()
            print("aiohttp 出口 IP:", data["ip"])

asyncio.run(main())
```

---

### 3. 自动化测试与无头浏览器

#### Playwright (Python)

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # 直接连接本地自愈网关
    browser = p.chromium.launch(
        headless=True,
        proxy={"server": "http://127.0.0.1:10801"}
    )
    page = browser.new_page()
    page.goto("https://ipinfo.io/json")
    print("Playwright 探测出口:", page.inner_text("pre"))
    browser.close()
```

#### DrissionPage

```python
from DrissionPage import ChromiumPage, ChromiumOptions

co = ChromiumOptions()
co.set_proxy("http://127.0.0.1:10801")
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
  # 接入本地自动换线网关
  - name: "URnetwork-Local-Gateway"
    type: socks5
    server: 127.0.0.1
    port: 10801
    udp: false

  # 或直连云端独立节点
  - name: "URnetwork-Cloud-SOCKS5"
    type: socks5
    server: node3.bringyour.com
    port: 11080
    username: tok_abc123
    password: none
```

#### sing-box Outbounds JSON 配置片段

```json
{
  "outbounds": [
    {
      "type": "socks",
      "tag": "urnetwork-local",
      "server": "127.0.0.1",
      "server_port": 10801,
      "version": "5"
    }
  ]
}
```

---

## 📦 Python SDK 编程式调用

除了通过 Web 控制台与终端 CLI，你还可以直接在代码中引用底层核心模块做深度定制开发：

```python
from core.client import URClient
from core.config import load_config

# 1. 自动加载本地配置
config = load_config()

# 2. 初始化官方 API 客户端
client = URClient(
    api_key=config.get("api_key"),
    timeout=15
)

# 3. 登录并查询账户配额
info = client.login()
print(f"登录成功: 网络 [{info['network_name']}] | 用户ID: {info['user_id']}")

status = client.get_account_status()
print(f"今日总额度: {status['daily_quota_gib']} GiB | 剩余: {status['remaining_gib']} GiB")

# 4. 申请指定国家住宅代理节点 (如日本 JP)
proxy = client.create_proxy(mode="country", target="JP")
print(f"分配成功! 出口主机: {proxy['host']}")
print(f"SOCKS5: {proxy['standard_socks5']}")
print(f"HTTP:   {proxy['standard_http']}")

# 5. 使用完毕后精准注销该实例 (释放云端连接额度)
client.remove_proxy(proxy["client_id"])
print("已成功释放该住宅节点实例！")
```

---

## 📂 完整项目目录结构

```text
URnetwork-Proxy/
├── config.json                 # 核心配置文件 (实际生效配置，支持 JSONC 格式与热加载)
├── config.example.json         # 带详尽注释的配置模板 (JSONC 规范)
├── main.py                     # 程序总入口 (CLI 命令行与 Web 控制台引导)
├── requirements.txt            # 依赖说明清单 (0 外部依赖，原生标准库运行)
├── start.sh                    # Linux / macOS 交互式 CLI 启动脚本 (可执行)
├── start_web.sh                # Linux / macOS Web 控制台启动脚本 (可执行)
├── start.bat                   # Windows 快速启动交互式 CLI 脚本
├── start_web.bat               # Windows 快速启动 Web 控制台脚本
├── systemd/                    # Linux 生产环境系统级托管配置
│   └── urnetwork-proxy.service # Systemd 后台服务单元模板
├── core/                       # 核心业务与网络引擎
│   ├── __init__.py
│   ├── checker.py              # 隧道探测与自检引擎 (TCP Ping、SOCKS5/HTTP 真实出口 IP 握手反查)
│   ├── client.py               # 官方 API 客户端 (五重鉴权、配额管理、代理生命周期与云端抽屉注销)
│   ├── config.py               # 配置加载与持久化器 (JSONC 注释剥离、容错清洗与原子写入)
│   ├── config_editor.py        # 双模式配置中心后端引擎 (表单与 JSONC 互转、字段校验、版本冲突保护)
│   ├── gateway.py              # 本地多网关生命周期管理引擎 (多端口管理、健康自检、防抖换线、故障自愈)
│   ├── locator.py              # 全球网络拓扑探测器 (抓取 90+ 国 Provider 实时分布、城市模糊检索)
│   ├── multiplexer.py          # 单端口协议复用器 (同端口自动嗅探分流 HTTP、HTTPS CONNECT 与 SOCKS5)
│   ├── protocol.py             # 底层协议解析器 (SOCKS5 / HTTP 报文解析、字节级地址嗅探)
│   ├── storage.py              # 原子存储与进程锁控制 (跨平台原子写盘、POSIX flock / Windows msvcrt)
│   └── web_server.py           # 内置本地 Web 管理服务器 (REST API、静态资源托管、会话鉴权)
├── web/                        # 现代化 Web 控制台前端资源 (纯原生轻量实现)
│   ├── index.html              # 控制台单页面入口 (网关卡片、拓扑展示、配置面板、配额监控)
│   ├── app.js                  # 控制台交互逻辑 (状态轮询、双向数据绑定、动态表单校验)
│   └── style.css               # 响应式界面样式 (卡片化布局、自适应移动端与桌面端)
├── docs/                       # 架构设计与技术规格文档
│   ├── architecture.md         # 系统全景架构设计文档
│   ├── gateway-guide.md        # 多网关与单端口复用深度使用指南
│   ├── config-panel-design.md  # 双模式配置面板设计与安全规范
│   └── assets/                 # 架构图示与控制台高清截图
└── tests/                      # 自动化测试套件 (覆盖网关、协议复用、配置中心、Web 服务等 98 个用例)
```

---

## 🧪 自动化测试与质量验证

项目包含严密的自动化测试套件，涵盖协议复用、多网关自愈、配置中心与 Web 服务交互：

```bash
# 执行全量自动化单元与集成测试 (默认包含 98 个测试用例，耗时约 20 秒)
python3 -m unittest discover tests

# 静态字节码语法编译检查
python3 -m compileall -q core main.py tests
```

---

## ❓ 常见问题与避坑指南 (FAQ)

### Q1: 为什么一个网关端口能同时处理 HTTP 和 SOCKS5？
> **答：** 底层 `MixedProxyServer` 采用了首字节协议嗅探（First-Byte Sniffing）机制。当客户端发起 TCP 连接时，网关只读预读首个字节：
> - 若首字节为 `0x05`，立即转入 RFC 1928 标准 SOCKS5 协商流程；
> - 若首字节为大写 ASCII 字符（如 `G`ET、`P`OST、`C`ONNECT），立即进入标准 HTTP / HTTPS 隧道处理流程。
> 因此客户端向同一个端口（如 `127.0.0.1:10801`）配置 HTTP 或 SOCKS5 代理均可直接通畅工作。

### Q2: 为什么直连模式下密码固定填入 `none`？
> **答：** URnetwork 官方节点由 `auth_token` 唯一定位身份。根据 RFC 1929 规范，很多客户端（Python `PySocks`、Clash、Chrome 插件）若遇到密码为空字符串时，会**自动将鉴权子协商降级为 0x00 (无鉴权)**，导致远端网关直接拒绝连接。统一规范填充 `none` 可确保客户端强制发送鉴权子协议，最大化保障第三方兼容性。

### Q3: 在无桌面的 Linux 云服务器上运行，如何访问 Web 控制台？
> **答：** 
> 1. Web 控制台为保护用户凭据，默认仅在回环地址（`127.0.0.1`）监听。
> 2. 在本地电脑通过 SSH 隧道进行端口转发即可安全访问：
>    ```bash
>    ssh -L 8777:127.0.0.1:8777 user@your-server-ip
>    ```
> 3. 打开本地电脑浏览器访问 `http://127.0.0.1:8777/`，使用管理用户名 / 密码登录（初始为 `admin / admin`）。远程使用请修改默认密码，并通过 SSH 隧道或 HTTPS 反向代理保护登录链路。

### Q4: Google 账号快捷登录没有 24 位助记词，该如何使用？
> **答：**
> 1. 打开 [https://ur.io](https://ur.io) 登录，在界面中获取一个 5 分钟有效的临时 `Auth Code`；
> 2. 执行 `./start.sh --auth-code 你的授权码`，程序会自动用此授权码换取长期有效的官方 API Key 并自动保存进 `config.json`；
> 3. 若需要备份专属助记词，也可以在交互菜单中选择 `[8] 凭证管理 -> [3] 生成 24 位专属助记词`。

### Q5: 网关自动换线时，正在进行的爬虫请求会不会直接中断？
> **答：** 不会！`GatewayEngine` 在换线时会执行平滑过渡：
> - 发现健康异常后，保持旧链路工作，在后台异步向云端申请新节点并执行完整的双探针检测；
> - 新节点握手完全成功后，执行原子指针切换；**此后所有新建立的连接立即走新节点**；
> - 旧节点保留 30 秒排空保护期（Drain Period），允许正在传输数据的旧长连接从容完成，避免业务断流。

### Q6: 为什么有时搜索特定城市会提示“降级为最佳连接”？
> **答：** 去中心化住宅网络的节点是由全球家庭宽带 Provider 提供的，某些小众城市在特定时段可能暂时没有 Provider 在线。为确保业务平稳不中断，工具会智能捕获未命中状态，平滑降级为全球最佳链路（Best Available）。

---

## 📄 开源协议与免责声明

- 本项目采用 [MIT 许可证](LICENSE) 开源。
- **免责声明：** 本项目仅作为与 URnetwork (ur.io) 官方 API 进行交互的客户端辅助管理工具，所有网络节点与住宅出口均由 URnetwork 官方及网络提供者提供。使用者在使用本工具时应严格遵守当地法律法规以及服务商的服务条款，严禁用于任何非法入侵、网络攻击或侵犯隐私等违法活动。开发者不对因使用本工具造成的任何直接或间接后果承担法律责任。

#!/usr/bin/env python3
"""
URnetwork 住宅代理交互式管理工具
基于 URnetwork 去中心化住宅网络，支持多重认证登录 (API Key / 助记词 / 网页授权码 / JWT / 密码)、
实时国家抓取、即用测试命令生成与端到端代理连通性自检
"""

import sys
import io

if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
from core.config import load_config, save_config
from core.client import URClient
from core.locator import fetch_live_countries, search_cities
from core.checker import test_tcp_ping, test_full_proxy

HOT_COUNTRIES = ["US", "JP", "DE", "GB", "SG", "HK", "FR", "CA", "KR", "AU"]

def safe_input(prompt=""):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None

def print_banner():
    print("""
======================================================================
         URnetwork 去中心化住宅代理生成与管理工具 (ur.io)
======================================================================
""")

def display_proxy_result(res, check_tunnel=True):
    host = res["host"]
    hp = res["http_port"]
    sp = res["socks_port"]
    tok = res["auth_token"]
    pwd = res.get("password", "none")
    cmds = res.get("test_commands", {})

    print("\n" + "=" * 70)
    print(f"【节点分配成功 - 出口策略: {res['target_desc']}】")
    print(f"  网页标识名称: {res.get('description', 'resident proxy')} (Client ID: {res.get('client_id')})")
    print("-" * 70)
    print("【标准格式代理 (用户名:密码@Host:端口)】")
    print(f"  HTTP 代理:   {res['standard_http']}")
    print(f"  SOCKS5 代理: {res['standard_socks5']}")
    print(f"  HTTPS 代理:  {res['standard_https']}")
    print("  * 提示: 密码统一填充为 'none'，避免客户端因空密码降级导致鉴权被拒。")
    print("-" * 70)
    print("【即用型测试命令 (可直接在终端中复制运行)】")
    if "curl_http" in cmds:
        print(f"  cURL (HTTP):     {cmds['curl_http']}")
    if "curl_socks5" in cmds:
        print(f"  cURL (SOCKS5):   {cmds['curl_socks5']}")

    if not check_tunnel:
        print("=" * 70 + "\n")
        return

    print("-" * 70)
    print("【端到端出口 IP 深度自检】")
    print(f"[*] 正在握手自检 SOCKS5 端口 ({host}:{sp}) 与 HTTP 端口 ({host}:{hp})...")
    check_res = test_full_proxy(host, hp, sp, tok, pwd, timeout=12)
    if check_res["socks_ok"]:
        print(f"  [+] SOCKS5 握手成功! 出口 IP: {check_res['socks_result']} (延迟: {check_res['socks_ms']}ms)")
    else:
        print(f"  [-] SOCKS5 握手失败: {check_res['socks_result']}")

    if check_res["http_ok"]:
        print(f"  [+] HTTP 握手成功!   出口 IP: {check_res['http_result']} (延迟: {check_res['http_ms']}ms)")
    else:
        print(f"  [-] HTTP 握手失败:   {check_res['http_result']}")
    print("=" * 70 + "\n")

def auth_management_menu(client, config):
    """账号凭证与密钥管理子菜单"""
    while True:
        print("\n" + "=" * 50)
        print("         🔑 URnetwork 账号凭证与密钥管理")
        print("=" * 50)
        status = client.network_info
        print(f" 当前认证方式: {client.auth_method.upper()}")
        print(f" 网络名称:     {status.get('network_name', '未知')}")
        print(f" 网络 ID:       {status.get('network_id', '未知')}")
        print("-" * 50)
        print(" [1] ⚡ 生成官方长期 API Key (urn_...) 并保存为默认")
        print(" [2] 📋 查看当前账号名下的所有 API Key 列表")
        print(" [3] 🧬 为当前账号生成专属 24 位助记词 (Google/邮箱用户推荐)")
        print(" [4] 🔄 重新生成/重置助记词")
        print(" [5] 🔀 切换或更换登录凭证 (Auth Code / 助记词 / Token)")
        print(" [0] 返回主菜单")
        print("-" * 50)

        sub_choice = safe_input("请选择操作 [0-5]: ")
        if sub_choice in [None, "0"]:
            break

        if sub_choice == "1":
            key_name = safe_input("请输入 API Key 描述名称 [默认: URnetwork-Proxy]: ")
            if key_name is None:
                break
            key_name = key_name.strip() or "URnetwork-Proxy"
            print(f"[*] 正在为网络 [{status.get('network_name')}] 创建 API Key...")
            try:
                res = client.create_api_key(name=key_name)
                new_key = res.get("api_key")
                print(f"[+] 成功生成长期 API Key:\n    {new_key}")
                config["api_key"] = new_key
                save_config(config)
                print("[+] 已自动写入 config.json 的 api_key 中，后续将优先使用该密钥！")
            except Exception as e:
                print(f"[!] 创建失败: {e}")
            safe_input("按回车键继续...")

        elif sub_choice == "2":
            print("[*] 正在查询账号名下的 API Key 列表...")
            try:
                keys = client.list_api_keys()
                if not keys:
                    print("[i] 当前账号尚未创建任何 API Key。")
                else:
                    print(f"共找到 {len(keys)} 个 API Key:")
                    for idx, k in enumerate(keys, 1):
                        print(f" [{idx}] ID: {k.get('id')} | 名称: {k.get('name')} | 创建时间: {k.get('create_time')}")
            except Exception as e:
                print(f"[!] 查询失败: {e}")
            safe_input("按回车键继续...")

        elif sub_choice == "3":
            print("[*] 正在向官方请求为当前账号生成 24 位恢复助记词...")
            try:
                phrase = client.generate_seedphrase()
                print("\n" + "=" * 60)
                print("【24 位恢复助记词生成成功】")
                print(phrase)
                print("=" * 60)
                print("* 请务必妥善保存该助记词，切勿泄露给他人。")
                save_it = safe_input("是否将该助记词保存到 config.json? (y/N): ")
                if save_it and save_it.strip().lower() == "y":
                    config["seedphrase"] = phrase
                    save_config(config)
                    print("[+] 已成功保存至 config.json！")
            except Exception as e:
                print(f"[!] 生成助记词失败: {e}")
            safe_input("按回车键继续...")

        elif sub_choice == "4":
            confirm = safe_input("⚠️ 警告: 重置助记词将替换旧助记词，确认继续? (yes/no): ")
            if confirm and confirm.strip().lower() == "yes":
                try:
                    phrase = client.regenerate_seedphrase()
                    print("\n[+] 助记词重置成功:\n" + phrase)
                    config["seedphrase"] = phrase
                    save_config(config)
                    print("[+] 已更新 config.json。")
                except Exception as e:
                    print(f"[!] 重置失败: {e}")
            safe_input("按回车键继续...")

        elif sub_choice == "5":
            print("\n选择你要录入的凭证类型:")
            print(" [1] 网页授权码 (Auth Code，去 https://ur.io/app/account/settings 生成)")
            print(" [2] 官方 API Key (urn_...)")
            print(" [3] 浏览器导出的 JWT (byJwt)")
            print(" [4] 24 位助记词 (Seedphrase)")
            auth_type = safe_input("请选择 [1-4]: ")
            if auth_type == "1":
                code = safe_input("请输入网页端生成的 Auth Code: ")
                if code and code.strip():
                    try:
                        new_c = URClient(auth_code=code.strip(), api_base=config.get("api_base_url"))
                        new_c.login()
                        print(f"[+] 授权成功! 对应网络: {new_c.network_info.get('network_name')}")
                        k_res = new_c.create_api_key(name="AuthCode-Converted-Key")
                        config["api_key"] = k_res.get("api_key")
                        save_config(config)
                        client.api_key = config["api_key"]
                        client.login()
                        print("[+] 已自动转换为长期 API Key 并保存至 config.json！")
                    except Exception as e:
                        print(f"[!] 授权失败: {e}")
            elif auth_type == "2":
                k = safe_input("请输入 API Key (如 urn_...): ")
                if k and k.strip():
                    config["api_key"] = k.strip()
                    save_config(config)
                    client.api_key = k.strip()
                    client.login()
                    print("[+] API Key 已更新并生效。")
            elif auth_type == "3":
                t = safe_input("请输入 JWT Token: ")
                if t and t.strip():
                    config["jwt_token"] = t.strip()
                    save_config(config)
                    client.jwt_token = t.strip()
                    client.login()
                    print("[+] JWT Token 已更新并生效。")
            elif auth_type == "4":
                s = safe_input("请输入 24 位助记词 (空格分隔): ")
                if s and s.strip():
                    config["seedphrase"] = s.strip()
                    save_config(config)
                    client.seedphrase = s.strip()
                    client.login()
                    print("[+] 助记词已更新并生效。")
            safe_input("按回车键继续...")

def interactive_mode(client, config):
    while True:
        print_banner()
        st = client.network_info
        print(f"当前账户: {st.get('network_name', '未知')} [认证方式: {client.auth_method.upper()}]")
        print("-" * 70)
        print("请选择操作：")
        print(" [1] ⚡ 自动选择最佳连接 (Best Available)")
        print(" [2] 🌍 快速选择热门国家 (US, JP, DE, GB, SG, HK...)")
        print(" [3] 📋 实时从 https://ur.io 拉取完整国家列表并选择")
        print(" [4] 🔍 搜索具体城市/地区 (如 Tokyo, Frankfurt, London...)")
        print(" [5] 📊 查看当前账号配额与使用量")
        print(" [6] 📑 查看当前网页端活跃代理列表 (PROXIES)")
        print(" [7] 🧹 清理并重置所有代理与历史客户端 (完全清空网页 Proxies)")
        print(" [8] 🔑 账号凭证与密钥管理 (API Key / 助记词 / 网页授权)")
        print(" [0] 退出")
        print("-" * 70)

        choice = safe_input("请输入选项 [0-8]: ")
        if choice is None or choice == "0":
            print("\n已退出。感谢使用 URnetwork-Proxy！")
            break

        if choice == "1":
            print("\n[*] 正在申请全球最佳链路节点...")
            try:
                res = client.create_proxy(mode="best")
                display_proxy_result(res, check_tunnel=True)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "2":
            print("\n热门国家列表：")
            for idx, c in enumerate(HOT_COUNTRIES, 1):
                print(f" [{idx:2d}] {c}")
            c_pick = safe_input(f"\n请选择序号 [1-{len(HOT_COUNTRIES)}] 或直接输入国家代码 (如 JP) [默认 US]: ")
            if c_pick is None:
                break
            c_pick = c_pick.strip().upper() or "US"
            target_country = "US"
            if c_pick.isdigit() and 1 <= int(c_pick) <= len(HOT_COUNTRIES):
                target_country = HOT_COUNTRIES[int(c_pick) - 1]
            elif len(c_pick) == 2 and c_pick.isalpha():
                target_country = c_pick

            print(f"\n[*] 正在申请国家 [{target_country}] 节点...")
            try:
                res = client.create_proxy(mode="country", target=target_country)
                display_proxy_result(res, check_tunnel=True)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "3":
            print("\n[*] 正在从 https://ur.io 拉取最新在线国家与节点分布...")
            try:
                countries = fetch_live_countries(ur_base=config.get("ur_base_url", "https://ur.io"))
            except Exception as e:
                print(f"[!] 拉取失败: {e}")
                if safe_input("按回车键继续...") is None:
                    break
                continue

            print(f"\n成功获取 {len(countries)} 个国家/地区：")
            for idx, c in enumerate(countries[:20], 1):
                print(f" [{idx:2d}] [{c['code']}] {c['name']:<22} (在线节点: {c['providers']})")

            c_pick = safe_input(f"\n请选择国家序号 [1-20] 或直接输入国家代码: ")
            if c_pick is None:
                break
            c_pick = c_pick.strip().upper()
            target_country = "US"
            if c_pick.isdigit() and 1 <= int(c_pick) <= min(20, len(countries)):
                target_country = countries[int(c_pick) - 1]["code"]
            elif len(c_pick) == 2 and c_pick.isalpha():
                target_country = c_pick

            print(f"\n[*] 正在申请国家 [{target_country}] 节点...")
            try:
                res = client.create_proxy(mode="country", target=target_country)
                display_proxy_result(res, check_tunnel=True)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "4":
            city_query = safe_input("\n请输入要搜索的城市或地区英文名称 (例如 Tokyo, Frankfurt, London, Seoul): ")
            if city_query is None:
                break
            city_query = city_query.strip()
            if not city_query:
                continue

            print(f"[*] 正在检索 [{city_query}] 在线住宅节点...")
            cities = search_cities(
                city_query,
                api_base=config.get("api_base_url", "https://api.bringyour.com")
            )
            if not cities:
                print(f"[!] 未找到包含活跃节点的城市: {city_query}")
                if safe_input("按回车键继续...") is None:
                    break
                continue

            print(f"\n找到以下匹配城市节点：")
            for idx, loc in enumerate(cities[:5], 1):
                print(f" [{idx}] {loc.get('name')}, {loc.get('country')} (代码: {loc.get('country_code')}) | 在线节点: {loc.get('provider_count')}")

            pick = safe_input("\n请选择城市序号 [默认 1]: ")
            if pick is None:
                break
            pick = pick.strip() or "1"
            if pick.isdigit() and 1 <= int(pick) <= len(cities):
                target_loc = cities[int(pick) - 1]
                target_name = target_loc.get("name")
                print(f"\n[*] 正在申请城市 [{target_name}] 节点...")
                try:
                    res = client.create_proxy(mode="city", target=target_name)
                    display_proxy_result(res, check_tunnel=True)
                except Exception as e:
                    print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "5":
            print("\n[*] 正在获取当前账户配额与网络状态...")
            try:
                status = client.get_account_status()
                print("\n" + "=" * 50)
                print(f" 网络名称 (Network):  {status['network_name']}")
                print(f" 认证方式 (Auth):     {status['auth_method'].upper()}")
                print(f" 账户级别 (Plan):     {status['plan_tier'].capitalize()}")
                print(f" 每日总配额 (Quota):  {status['daily_quota_gib']} GiB")
                print(f" 剩余流量 (Balance):  {status['remaining_gib']} GiB")
                print(f" 今日已用 (Used):     {status['used_gib']} GiB")
                print(f" 访客模式 (Guest):    {'是' if status['is_guest'] else '否'}")
                print("=" * 50 + "\n")
            except Exception as e:
                print(f"[!] 查询失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "6":
            print("\n[*] 正在同步 https://ur.io 网页端 PROXIES 列表...")
            try:
                proxies = client.list_proxies()
                if not proxies:
                    print("[i] 网页端当前无活跃代理 (PROXIES 为空)。")
                else:
                    print(f"\n当前网页端活跃代理 (共 {len(proxies)} 个)：")
                    print("-" * 65)
                    for idx, p in enumerate(proxies, 1):
                        print(f" [{idx}] {p['description']} | 协议标签: {p['protocol']}")
                        print(f"      Client ID:   {p['client_id']}")
                        print(f"      Host / 端口: {p['host']}:{p['socks_port']} (SOCKS5) | {p['http_port']} (HTTP) | {p['https_port']} (HTTPS)")
                    print("-" * 65)
            except Exception as e:
                print(f"[!] 获取失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "7":
            print("\n[*] 正在深度清理网页端 Proxies 列表及历史客户端连接...")
            try:
                res = client.clean_clients()
                print(f"[+] 清理完成！")
                print(f"    - 成功删除网页端 PROXIES 代理实例: {res['proxies_removed']} 个")
                print(f"    - 并发客户端会话重置: {'成功' if res['clients_reset'] else '未变动'}")
                print("    (提示: 刷新 https://ur.io 网页，右侧 Proxies 列表将同步变为空)")
            except Exception as e:
                print(f"[!] 清理异常: {e}")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "8":
            auth_management_menu(client, config)

def prompt_initial_auth(config):
    """当配置文件无有效凭证时，引导用户输入"""
    print_banner()
    print("未检测到有效的账号认证凭证！请选择登录方式：\n")
    print(" [1] ⚡ 网页授权码登录 (推荐 Google/邮箱用户，访问 https://ur.io/app/account/settings 获取)")
    print(" [2] 🔑 官方长期 API Key (urn_...)")
    print(" [3] 📋 24 位恢复助记词 (Seedphrase)")
    print(" [4] 🌐 浏览器提取的 JWT Token (byJwt)")
    print(" [5] 📧 邮箱与独立密码登录")
    print(" [0] 退出")
    print("-" * 70)

    choice = safe_input("请选择 [0-5]: ")
    if choice in [None, "0"]:
        sys.exit(0)

    if choice == "1":
        code = safe_input("请输入 5 分钟网页 Auth Code: ")
        if code and code.strip():
            c = URClient(auth_code=code.strip(), api_base=config.get("api_base_url"))
            c.login()
            print(f"[+] 登录成功! 对应网络: {c.network_info.get('network_name')}")
            key_res = c.create_api_key(name="URnetwork-Proxy-Cli")
            config["api_key"] = key_res.get("api_key")
            save_config(config)
            print(f"[+] 已自动换取长期 API Key 并写入 config.json！")
            return c
    elif choice == "2":
        k = safe_input("请输入 API Key (urn_...): ")
        if k and k.strip():
            config["api_key"] = k.strip()
            save_config(config)
            c = URClient(api_key=k.strip(), api_base=config.get("api_base_url"))
            c.login()
            return c
    elif choice == "3":
        s = safe_input("请输入 24 位助记词 (空格分隔): ")
        if s and s.strip():
            config["seedphrase"] = s.strip()
            save_config(config)
            c = URClient(seedphrase=s.strip(), api_base=config.get("api_base_url"))
            c.login()
            return c
    elif choice == "4":
        t = safe_input("请输入 JWT Token: ")
        if t and t.strip():
            config["jwt_token"] = t.strip()
            save_config(config)
            c = URClient(jwt_token=t.strip(), api_base=config.get("api_base_url"))
            c.login()
            return c
    elif choice == "5":
        e = safe_input("请输入邮箱: ")
        p = safe_input("请输入密码: ")
        if e and p:
            c = URClient(email=e.strip(), password=p.strip(), api_base=config.get("api_base_url"))
            c.login()
            config["email"] = e.strip()
            config["password"] = p.strip()
            save_config(config)
            return c

    print("[!] 认证信息无效，退出。")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="URnetwork 住宅代理管理器 (ur.io)")
    # 鉴权参数
    parser.add_argument("--api-key", default="", help="官方长期 API Key (urn_...)")
    parser.add_argument("--auth-code", default="", help="网页端生成的 5 分钟临时授权码 (自动换取长期 API Key)")
    parser.add_argument("--jwt", default="", help="浏览器导出的 JWT Token (byJwt)")
    parser.add_argument("--seedphrase", default="", help="24 位助记词")
    parser.add_argument("--email", default="", help="邮箱账号")
    parser.add_argument("--password", default="", help="邮箱密码")
    # 凭证管理
    parser.add_argument("--create-api-key", nargs="?", const="URnetwork-Proxy-Cli", help="为当前账号生成长期 API Key")
    parser.add_argument("--generate-seedphrase", action="store_true", help="为当前账号生成 24 位专属助记词")
    # 代理控制与查询
    parser.add_argument("--mode", choices=["best", "country", "city"], help="代理策略模式: best | country | city")
    parser.add_argument("--target", default="", help="目标国家代码 (如 JP) 或城市名 (如 Tokyo)")
    parser.add_argument("--no-check", action="store_true", help="跳过端到端代理连通性自检 (仅显示连接信息)")
    parser.add_argument("--list-countries", action="store_true", help="从 https://ur.io 获取在线国家列表")
    parser.add_argument("--status", action="store_true", help="查看账户配额状态")
    parser.add_argument("--list-proxies", action="store_true", help="查看当前网页端 Proxies 列表")
    parser.add_argument("--clean", action="store_true", help="清理所有网页端代理与历史客户端连接")
    args = parser.parse_args()

    config = load_config()

    api_key = args.api_key or config.get("api_key", "").strip()
    jwt_token = args.jwt or config.get("jwt_token", "").strip()
    seedphrase = args.seedphrase or config.get("seedphrase", "").strip()
    auth_code = args.auth_code.strip() if args.auth_code else ""
    email = args.email or config.get("email", "").strip()
    password = args.password or config.get("password", "").strip()

    client = None
    has_credentials = any([api_key, jwt_token, seedphrase, auth_code, (email and password)])

    if not has_credentials:
        if not args.list_countries:
            client = prompt_initial_auth(config)
    else:
        client = URClient(
            api_key=api_key,
            jwt_token=jwt_token,
            seedphrase=seedphrase,
            auth_code=auth_code,
            email=email,
            password=password,
            api_base=config.get("api_base_url", "https://api.bringyour.com"),
            timeout=config.get("timeout_seconds", 15)
        )

    if auth_code and client:
        try:
            client.login()
            key_res = client.create_api_key(name="CLI-AuthCode-Key")
            config["api_key"] = key_res.get("api_key")
            save_config(config)
            print(f"[+] Auth Code 验证成功！已自动换取长期 API Key 并写入 config.json: {config['api_key']}")
        except Exception as e:
            print(f"[!] Auth Code 转换失败: {e}")
            sys.exit(1)

    if args.create_api_key and client:
        try:
            client.login()
            key_res = client.create_api_key(name=args.create_api_key)
            print(f"[+] 创建成功! 长期 API Key:\n    {key_res.get('api_key')}")
            print(f"    (ID: {key_res.get('id')})")
        except Exception as e:
            print(f"[!] 创建 API Key 失败: {e}")
        return

    if args.generate_seedphrase and client:
        try:
            client.login()
            phrase = client.generate_seedphrase()
            print(f"[+] 专属助记词生成成功:\n    {phrase}")
        except Exception as e:
            print(f"[!] 生成助记词失败: {e}")
        return

    if args.status and client:
        client.login()
        st = client.get_account_status()
        print(f"Network: {st['network_name']} | Auth: {st['auth_method'].upper()} | Quota: {st['daily_quota_gib']} GiB | Remaining: {st['remaining_gib']} GiB")
        return

    if args.list_proxies and client:
        client.login()
        proxies = client.list_proxies()
        print(f"网页端活跃代理数: {len(proxies)}")
        for idx, p in enumerate(proxies, 1):
            print(f"[{idx}] {p['description']} ({p['protocol']}) -> {p['host']}:{p['socks_port']} [ID: {p['client_id']}]")
        return

    if args.clean and client:
        client.login()
        res = client.clean_clients()
        print(f"[+] 清理完成: 已移除 {res['proxies_removed']} 个代理实例，客户端重置: {res['clients_reset']}")
        return

    if args.list_countries:
        print("[*] 正在从 https://ur.io 获取最新国家节点...")
        countries = fetch_live_countries(ur_base=config.get("ur_base_url", "https://ur.io"))
        for c in countries[:25]:
            print(f"[{c['code']}] {c['name']:<25} 节点数: {c['providers']}")
        return

    if args.mode and client:
        client.login()
        target = args.target or (config.get("default_country", "US") if args.mode == "country" else "")
        res = client.create_proxy(mode=args.mode, target=target)
        display_proxy_result(res, check_tunnel=not args.no_check)
        return

    if client:
        client.login()
        interactive_mode(client, config)

if __name__ == "__main__":
    main()

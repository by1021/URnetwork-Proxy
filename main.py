#!/usr/bin/env python3
"""
URnetwork 住宅代理交互式管理工具
基于 URnetwork 去中心化住宅网络，支持实时国家抓取、即用测试命令生成与端到端代理连通性自检
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
    if "python_http" in cmds:
        print(f"  Python (HTTP):   {cmds['python_http']}")
    if "python_socks5" in cmds:
        print(f"  Python (SOCKS5): {cmds['python_socks5']}")
    print("-" * 70)
    print("【官方原生返回 URL】(注意: 原生 URL 默认不含 Basic 鉴权信息)")
    print(f"  原生 HTTPS:  {res['native_https_url']}")
    print(f"  原生 HTTP:   {res['native_http_url']}")
    print(f"  原生 SOCKS5: {res['native_socks_url']}")
    print("-" * 70)

    if check_tunnel:
        print("[*] 正在执行端到端代理隧道握手与出口 IP 自检 (基于 api.ipify.org)...")
        t_res = test_full_proxy(host, hp, sp, tok, pwd, timeout=8)
        
        if t_res["http_ok"]:
            h_stat = f"在线 [OK] ({t_res['http_ms']} ms) -> 出口 IP: {t_res['http_result']}"
        else:
            h_stat = f"异常 [FAIL] ({t_res['http_result']})"

        if t_res["socks_ok"]:
            s_stat = f"在线 [OK] ({t_res['socks_ms']} ms) -> 出口 IP: {t_res['socks_result']}"
        else:
            s_stat = f"异常 [FAIL] ({t_res['socks_result']})"

        print(f"  HTTP 代理状态:   [{h_stat}]")
        print(f"  SOCKS5 代理状态: [{s_stat}]")
    else:
        # 仅做极速 TCP 端口自检
        ok_http, ms_http, err_http = test_tcp_ping(host, hp)
        ok_socks, ms_socks, err_socks = test_tcp_ping(host, sp)
        status_http = f"在线 ({ms_http} ms)" if ok_http else f"异常 ({err_http})"
        status_socks = f"在线 ({ms_socks} ms)" if ok_socks else f"异常 ({err_socks})"
        print(f"  端口底层健康: HTTP [{status_http}] | SOCKS5 [{status_socks}]")

    print("=" * 70 + "\n")

def interactive_mode(client, config):
    while True:
        print_banner()
        print("请选择操作：")
        print(" [1] ⚡ 自动选择最佳连接 (Best Available)")
        print(" [2] 🌍 快速选择热门国家 (US, JP, DE, GB, SG, HK...)")
        print(" [3] 📋 实时从 https://ur.io 拉取完整国家列表并选择")
        print(" [4] 🔍 搜索具体城市/地区 (如 Tokyo, Frankfurt, London...)")
        print(" [5] 📊 查看当前账号配额与使用量")
        print(" [6] 📑 查看当前网页端活跃代理列表 (PROXIES)")
        print(" [7] 🧹 清理并重置所有代理与历史客户端 (完全清空网页 Proxies)")
        print(" [0] 退出")
        print("-" * 70)

        raw_choice = safe_input("请输入选项编号 [0-7]: ")
        if raw_choice is None:
            print("\n[!] 检测到非交互式终端或标准输入已关闭 (EOF)。")
            print("    如需在脚本或自动化环境中使用，请通过命令行参数直接运行：")
            print("      python3 main.py --mode best                 # 申请全球最佳节点")
            print("      python3 main.py --mode country --target US  # 申请指定国家 (如 US, JP, DE)")
            print("      python3 main.py --mode city --target Tokyo  # 申请指定城市 (如 Tokyo, London)")
            print("      python3 main.py --status                    # 查看配额状态与剩余流量")
            print("      python3 main.py --list-proxies              # 查看当前网页端 Proxies 列表")
            print("      python3 main.py --clean                     # 一键清理所有网页 Proxies 与客户端")
            break

        choice = raw_choice.strip()
        if choice == "0":
            print("\n已退出。感谢使用 URnetwork 住宅代理管理器！")
            break

        elif choice == "1":
            print("\n[*] 正在申请 [全球最佳可用] 节点...")
            try:
                res = client.create_proxy(mode="best")
                display_proxy_result(res, check_tunnel=True)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "2":
            print("\n热门国家/地区列表：")
            for idx, c in enumerate(HOT_COUNTRIES, 1):
                print(f" [{idx:2d}] {c}")
            pick = safe_input(f"\n请选择国家序号 [1-{len(HOT_COUNTRIES)}] 或直接输入两字母代码 (默认 1/US): ")
            if pick is None:
                break
            pick = pick.strip().upper()
            target_country = "US"
            if pick.isdigit() and 1 <= int(pick) <= len(HOT_COUNTRIES):
                target_country = HOT_COUNTRIES[int(pick) - 1]
            elif len(pick) == 2 and pick.isalpha():
                target_country = pick

            print(f"\n[*] 正在申请指定国家 [{target_country}] 节点...")
            try:
                res = client.create_proxy(mode="country", target=target_country)
                display_proxy_result(res, check_tunnel=True)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "3":
            print("\n[*] 正在从 https://ur.io 实时抓取活跃国家与在线住宅节点数...")
            try:
                countries = fetch_live_countries(
                    ur_base=config.get("ur_base_url", "https://ur.io"),
                    api_base=config.get("api_base_url", "https://api.bringyour.com"),
                    timeout=config.get("timeout_seconds", 15)
                )
            except Exception as e:
                print(f"[!] 抓取失败: {e}")
                countries = []

            if not countries:
                print("[!] 未能拉取到实时国家数据，降级为默认常用列表。")
                countries = [{"code": c, "name": c, "providers": "多"} for c in HOT_COUNTRIES]

            print("\n" + "=" * 50)
            print(" 当前全球住宅节点分布 (Top 20 活跃国家):")
            print("=" * 50)
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

def main():
    parser = argparse.ArgumentParser(description="URnetwork 住宅代理管理器 (ur.io)")
    parser.add_argument("--mode", choices=["best", "country", "city"], help="代理策略模式: best | country | city")
    parser.add_argument("--target", default="", help="目标国家代码 (如 JP) 或城市名 (如 Tokyo)")
    parser.add_argument("--no-check", action="store_true", help="跳过端到端代理连通性自检 (仅显示连接信息)")
    parser.add_argument("--list-countries", action="store_true", help="从 https://ur.io 获取在线国家列表")
    parser.add_argument("--status", action="store_true", help="查看账户配额状态")
    parser.add_argument("--list-proxies", action="store_true", help="查看当前网页端 Proxies 列表")
    parser.add_argument("--clean", action="store_true", help="清理所有网页端代理与历史客户端连接")
    args = parser.parse_args()

    config = load_config()
    seedphrase = config.get("seedphrase", "").strip()
    if not seedphrase:
        print("[!] 错误: config.json 中未配置 seedphrase 助记词。请先编辑 config.json。")
        sys.exit(1)

    client = URClient(
        seedphrase=seedphrase,
        api_base=config.get("api_base_url", "https://api.bringyour.com"),
        timeout=config.get("timeout_seconds", 15)
    )

    if args.status:
        st = client.get_account_status()
        print(f"Network: {st['network_name']} | Quota: {st['daily_quota_gib']} GiB | Remaining: {st['remaining_gib']} GiB")
        return

    if args.list_proxies:
        proxies = client.list_proxies()
        print(f"网页端活跃代理数: {len(proxies)}")
        for idx, p in enumerate(proxies, 1):
            print(f"[{idx}] {p['description']} ({p['protocol']}) -> {p['host']}:{p['socks_port']} [ID: {p['client_id']}]")
        return

    if args.clean:
        res = client.clean_clients()
        print(f"[+] 清理完成: 已移除 {res['proxies_removed']} 个代理实例，客户端重置: {res['clients_reset']}")
        return

    if args.list_countries:
        print("[*] 正在从 https://ur.io 获取最新国家节点...")
        countries = fetch_live_countries(ur_base=config.get("ur_base_url", "https://ur.io"))
        for c in countries[:25]:
            print(f"[{c['code']}] {c['name']:<25} 节点数: {c['providers']}")
        return

    if args.mode:
        res = client.create_proxy(mode=args.mode, target=args.target)
        display_proxy_result(res, check_tunnel=not args.no_check)
        return

    # 无参数时进入交互式菜单
    interactive_mode(client, config)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
URnetwork 住宅代理交互式管理工具
基于 URnetwork 去中心化住宅网络，支持实时国家抓取与自选节点
"""

import sys
import argparse
from core.config import load_config, save_config
from core.client import URClient
from core.locator import fetch_live_countries, search_cities
from core.checker import test_tcp_ping

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

def display_proxy_result(res):
    print("\n" + "=" * 70)
    print(f"【节点分配成功 - 出口策略: {res['target_desc']}】")
    print("-" * 70)
    print("【标准格式代理 (用户名:密码@Host:端口)】")
    print(f"HTTP 代理:   {res['standard_http']}")
    print(f"SOCKS5 代理: {res['standard_socks5']}")
    print(f"HTTPS 代理:  {res['standard_https']}")
    print("-" * 70)
    print("【官方原生返回 URL】")
    print(f"原生 HTTPS:  {res['native_https_url']}")
    print(f"原生 HTTP:   {res['native_http_url']}")
    print(f"原生 SOCKS5: {res['native_socks_url']}")
    print("-" * 70)

    # 快速自检端口连通性
    ok_http, ms_http, err_http = test_tcp_ping(res["host"], res["http_port"])
    ok_socks, ms_socks, err_socks = test_tcp_ping(res["host"], res["socks_port"])
    status_http = f"在线 ({ms_http} ms)" if ok_http else f"异常 ({err_http})"
    status_socks = f"在线 ({ms_socks} ms)" if ok_socks else f"异常 ({err_socks})"

    print(f"端口健康状态: HTTP [{status_http}] | SOCKS5 [{status_socks}]")
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
        print(" [6] 🧹 清理历史闲置客户端 (释放并发槽位)")
        print(" [0] 退出")
        print("-" * 70)

        raw_choice = safe_input("请输入选项编号 [0-6]: ")
        if raw_choice is None:
            print("\n[!] 检测到非交互式终端或标准输入已关闭 (EOF)。")
            print("    如需在脚本或自动化环境中使用，请通过命令行参数直接运行：")
            print("      python3 main.py --mode best                 # 申请全球最佳节点")
            print("      python3 main.py --mode country --target US  # 申请指定国家 (如 US, JP, DE)")
            print("      python3 main.py --mode city --target Tokyo  # 申请指定城市 (如 Tokyo, London)")
            print("      python3 main.py --status                    # 查看配额状态与剩余流量")
            print("      python3 main.py --list-countries            # 列出全球在线国家列表")
            print("      python3 main.py --clean                     # 清理闲置并发客户端")
            print("      python3 main.py --help                      # 查看完整命令行帮助\n")
            break

        choice = raw_choice.strip()
        if choice == "0":
            print("再见！")
            break

        elif choice == "1":
            print("\n[*] 正在申请全球最佳链路节点...")
            try:
                res = client.create_proxy(mode="best")
                display_proxy_result(res)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "2":
            print("\n热门国家列表：")
            for idx, code in enumerate(HOT_COUNTRIES, 1):
                print(f"  [{idx:2d}] {code}")
            pick = safe_input("\n请输入序号或直接输入2位国家代码 (如 JP): ")
            if pick is None:
                break
            pick = pick.strip()
            if not pick:
                continue
            if pick.isdigit() and 1 <= int(pick) <= len(HOT_COUNTRIES):
                country_code = HOT_COUNTRIES[int(pick) - 1]
            else:
                country_code = pick.upper()
            
            print(f"\n[*] 正在申请指定国家 [{country_code}] 节点...")
            try:
                res = client.create_proxy(mode="country", target=country_code)
                display_proxy_result(res)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "3":
            print("\n[*] 正在连接 https://ur.io 实时抓取最新国家节点分布...")
            try:
                countries = fetch_live_countries(
                    ur_base=config.get("ur_base_url", "https://ur.io"),
                    api_base=config.get("api_base_url", "https://api.bringyour.com"),
                    timeout=config.get("timeout_seconds", 15)
                )
            except Exception as e:
                print(f"[!] 抓取国家列表失败: {e}")
                if safe_input("按回车键继续...") is None:
                    break
                continue

            print(f"成功获取 {len(countries)} 个国家及在线节点统计：\n")
            page_size = 25
            print(f"{'序号':<5} {'代码':<6} {'国家/地区名称':<28} {'在线节点':<10} {'覆盖区域'}")
            print("-" * 65)
            for idx, c in enumerate(countries[:page_size], 1):
                print(f"[{idx:2d}]   {c['code']:<6} {c['name']:<28} {c['providers']:<10} {c['regions_count']} 个区域")
            print("-" * 65)
            print(f"(展示前 {page_size} 个主要国家，输入代码可直接指定任意国家)")

            pick = safe_input("\n请输入序号选择，或直接输入国家代码 (如 JP / DE): ")
            if pick is None:
                break
            pick = pick.strip()
            if not pick:
                continue
            if pick.isdigit() and 1 <= int(pick) <= len(countries[:page_size]):
                selected_code = countries[int(pick) - 1]["code"]
            else:
                selected_code = pick.upper()

            print(f"\n[*] 正在申请指定国家 [{selected_code}] 代理节点...")
            try:
                res = client.create_proxy(mode="country", target=selected_code)
                display_proxy_result(res)
            except Exception as e:
                print(f"[!] 申请失败: {e}\n")
            if safe_input("按回车键继续...") is None:
                break

        elif choice == "4":
            city_query = safe_input("\n请输入要搜索的目标城市英文字符串 (如 Tokyo, London, Singapore): ")
            if city_query is None:
                break
            city_query = city_query.strip()
            if not city_query:
                continue
            print(f"\n[*] 正在联网检索城市 [{city_query}] 在线住宅节点...")
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
                    display_proxy_result(res)
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
            print("\n[*] 正在清理历史客户端连接...")
            if client.clean_clients():
                print("[+] 清理请求已提交成功！已重置并发客户端计数。")
            else:
                print("[!] 清理异常。")
            if safe_input("按回车键继续...") is None:
                break

def main():
    parser = argparse.ArgumentParser(description="URnetwork 住宅代理管理器 (ur.io)")
    parser.add_argument("--mode", choices=["best", "country", "city"], help="代理策略模式")
    parser.add_argument("--target", default="", help="目标国家代码 (如 JP) 或城市名 (如 Tokyo)")
    parser.add_argument("--list-countries", action="store_true", help="从 https://ur.io 获取在线国家列表")
    parser.add_argument("--status", action="store_true", help="查看账户配额状态")
    parser.add_argument("--clean", action="store_true", help="清理历史客户端连接")
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

    # 命令行直接调用参数
    if args.status:
        st = client.get_account_status()
        print(f"Network: {st['network_name']} | Quota: {st['daily_quota_gib']} GiB | Remaining: {st['remaining_gib']} GiB")
        return

    if args.clean:
        client.clean_clients()
        print("[+] 历史客户端清理成功。")
        return

    if args.list_countries:
        print("[*] 正在从 https://ur.io 获取最新国家节点...")
        countries = fetch_live_countries(ur_base=config.get("ur_base_url", "https://ur.io"))
        for c in countries[:25]:
            print(f"[{c['code']}] {c['name']:<25} 节点数: {c['providers']}")
        return

    if args.mode:
        res = client.create_proxy(mode=args.mode, target=args.target)
        display_proxy_result(res)
        return

    # 无参数时进入交互式菜单
    interactive_mode(client, config)

if __name__ == "__main__":
    main()

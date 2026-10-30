#!/usr/bin/env python3
"""
URnetwork 代理连通性与鉴权深度测试模块
包含底层 TCP 探测以及完整的 HTTP / SOCKS5 代理隧道真实握手自检 (纯标准库实现)
"""

import socket
import time
import base64

def test_tcp_ping(host, port, timeout=3):
    """测试代理服务器端口底层 TCP 连通性与延迟"""
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        latency_ms = round((time.time() - start) * 1000, 2)
        s.close()
        return True, latency_ms, None
    except Exception as e:
        return False, None, str(e)

def test_http_proxy(host, port, token, password="none", target_host="api.ipify.org", timeout=8):
    """
    通过 HTTP 代理隧道发起真实 GET 请求并提取住宅出口 IP
    返回: (ok, latency_ms, ip_or_error)
    """
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        auth_str = base64.b64encode(f"{token}:{password}".encode("utf-8")).decode("ascii")
        req = (
            f"GET http://{target_host}/ HTTP/1.1\r\n"
            f"Host: {target_host}\r\n"
            f"Proxy-Authorization: Basic {auth_str}\r\n"
            f"User-Agent: URnetwork-Checker/1.0\r\n"
            f"Connection: close\r\n\r\n"
        )
        s.sendall(req.encode("utf-8"))

        res = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            res += chunk
        s.close()

        latency_ms = round((time.time() - start) * 1000, 2)
        text = res.decode("utf-8", errors="ignore")
        header, _, body = text.partition("\r\n\r\n")
        first_line = header.splitlines()[0] if header else "无响应"
        
        if "200" in first_line:
            ip = body.strip()
            return True, latency_ms, ip
        return False, latency_ms, f"状态异常: {first_line}"
    except Exception as e:
        return False, None, str(e)

def test_socks5_proxy(host, port, token, password="none", target_host="api.ipify.org", timeout=8):
    """
    通过 SOCKS5 代理协议进行 RFC 1929 握手认证并请求出口 IP
    返回: (ok, latency_ms, ip_or_error)
    """
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))

        # 1. 协商阶段: 支持 RFC 1929 用户名/密码认证 (0x02)
        s.sendall(b"\x05\x01\x02")
        resp = s.recv(2)
        if len(resp) < 2 or resp[0] != 0x05 or resp[1] != 0x02:
            return False, None, f"协商认证方式被拒: {resp.hex() if resp else '无响应'}"

        # 2. RFC 1929 用户名密码子协商
        u_b = token.encode("utf-8")
        p_b = password.encode("utf-8")
        auth_pkt = b"\x01" + bytes([len(u_b)]) + u_b + bytes([len(p_b)]) + p_b
        s.sendall(auth_pkt)
        auth_resp = s.recv(2)
        if len(auth_resp) < 2 or auth_resp[1] != 0x00:
            return False, None, "SOCKS5 鉴权失败 (Token 或密码被拒)"

        # 3. 发起 CONNECT 请求 (ATYP 0x03 域名，端口 80)
        dom_b = target_host.encode("utf-8")
        conn_pkt = b"\x05\x01\x00\x03" + bytes([len(dom_b)]) + dom_b + (80).to_bytes(2, "big")
        s.sendall(conn_pkt)
        rep = s.recv(10)
        if len(rep) < 4 or rep[1] != 0x00:
            return False, None, f"SOCKS5 连接目标被拒: rep={rep[1] if len(rep)>=2 else '未知'}"

        # 4. 隧道建立成功，发送 HTTP GET 请求获取出口 IP
        http_req = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {target_host}\r\n"
            f"User-Agent: URnetwork-Checker/1.0\r\n"
            f"Connection: close\r\n\r\n"
        )
        s.sendall(http_req.encode("utf-8"))

        res = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            res += chunk
        s.close()

        latency_ms = round((time.time() - start) * 1000, 2)
        text = res.decode("utf-8", errors="ignore")
        header, _, body = text.partition("\r\n\r\n")
        first_line = header.splitlines()[0] if header else "无响应"

        if "200" in first_line:
            ip = body.strip()
            return True, latency_ms, ip
        return False, latency_ms, f"状态异常: {first_line}"
    except Exception as e:
        return False, None, str(e)

def test_full_proxy(host, http_port, socks_port, token, password="none", timeout=8):
    """完整自检代理 HTTP 与 SOCKS5 隧道"""
    http_ok, http_ms, http_ip = test_http_proxy(host, http_port, token, password, timeout=timeout)
    socks_ok, socks_ms, socks_ip = test_socks5_proxy(host, socks_port, token, password, timeout=timeout)
    return {
        "http_ok": http_ok,
        "http_ms": http_ms,
        "http_result": http_ip,
        "socks_ok": socks_ok,
        "socks_ms": socks_ms,
        "socks_result": socks_ip
    }

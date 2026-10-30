#!/usr/bin/env python3
"""
URnetwork 代理连通性测试模块
通过底层 Socket 探测代理端口 TCP 握手与响应速度
"""

import socket
import time

def test_tcp_ping(host, port, timeout=5):
    """测试代理服务器端口 TCP 连通性与延迟"""
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

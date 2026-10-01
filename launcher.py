"""Start the local Streamlit app on the first available nearby port."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path


HOST = "127.0.0.1"
FIRST_PORT = 8505
LAST_PORT = 8525


def available_port(first: int = FIRST_PORT, last: int = LAST_PORT) -> int:
    """Choose a free localhost port without touching any existing service."""
    for port in range(first, last + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((HOST, port))
        except OSError:
            continue
        return port
    raise RuntimeError(f"本机端口 {first}–{last} 均被占用，请关闭旧服务后重试。")


def main() -> int:
    try:
        port = available_port()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    if port != FIRST_PORT:
        print(f"端口 {FIRST_PORT} 已被占用，改用 {port}。", flush=True)
    print(f"浏览器地址：http://{HOST}:{port}", flush=True)
    command = [
        sys.executable,
        "-m", "streamlit", "run", "app.py",
        "--server.address", HOST,
        "--server.port", str(port),
        "--server.showEmailPrompt", "false",
        "--browser.gatherUsageStats", "false",
    ]
    try:
        return subprocess.call(command, cwd=Path(__file__).resolve().parent)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

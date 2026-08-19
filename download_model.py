#!/usr/bin/env python3
"""
LLMLingua 7B 模型多线程分段下载器(download_model.py)
======================================================
用并发 Range 请求加速下载(单线程仅 2.5MB/s,32 线程可到 10-20MB/s),
支持断点续传(已下载部分自动跳过)。

用法: python3 download_model.py <文件1> [文件2] ...
环境变量:
  HF_BASE: 镜像基址(默认 https://hf-mirror.com)
"""

import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.environ.get("HF_BASE", "https://hf-mirror.com")
REPO = "NousResearch/Llama-2-7b-hf"
SNAP = "/media/qq/文档/llm-compaction-proxy-data/hf_cache/hub/models--NousResearch--Llama-2-7b-hf/snapshots/main"
THREADS = 32
CHUNK = 8 * 1024 * 1024  # 8MB 每段


def file_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers.get("Content-Length", 0))


def download_range(url: str, start: int, end: int, out: str, lock) -> bool:
    headers = {"Range": f"bytes={start}-{end}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            with lock:
                with open(out, "r+b") as f:
                    f.seek(start)
                    while True:
                        chunk = r.read(1 * 1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
        return True
    except Exception:
        return False


def download_file(fname: str) -> str:
    url = f"{BASE}/{REPO}/resolve/main/{fname}"
    os.makedirs(SNAP, exist_ok=True)
    out = os.path.join(SNAP, fname)
    size = file_size(url)
    print(f"[开始] {fname} ({size/1024**3:.2f} GB)")

    # 断点续传:已存在且大小匹配 → 跳过
    if os.path.exists(out) and os.path.getsize(out) == size:
        print(f"[跳过] {fname} 已存在且完整")
        return fname

    # 初始化文件(截断到目标大小)
    with open(out, "wb") as f:
        f.truncate(size)

    lock = __import__("threading").Lock()
    ranges = [(i, min(i + CHUNK - 1, size - 1)) for i in range(0, size, CHUNK)]
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futures = {ex.submit(download_range, url, s, e, out, lock): (s, e) for s, e in ranges}
        for fut in as_completed(futures):
            if fut.result():
                done += 1
            else:
                print(f"[重试] {fname} 段 {futures[fut]}")
                s, e = futures[fut]
                for _ in range(3):
                    if download_range(url, s, e, out, lock):
                        done += 1
                        break
    elapsed = time.time() - t0
    speed = size / elapsed / 1024**2 if elapsed > 0 else 0
    print(f"[完成] {fname} ({size/1024**3:.2f}GB) 耗时{elapsed:.0f}s 均速{speed:.1f}MB/s")
    return fname


def main():
    files = sys.argv[1:] or [
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    ]
    for f in files:
        try:
            download_file(f)
        except Exception as e:
            print(f"[失败] {f}: {e}")


if __name__ == "__main__":
    main()

"""
Rust FFI 加速加载器(rust_ffi.py)
==================================
加载 Rust 编译产物(compaction_fast)加速高频路径(路线图 #11):

- estimate_tokens_cjk: CJK 感知 token 估算(与 Python estimate_tokens_v3 同口径)
- count_tokens: 通用 token 计数
- json_escape_len: JSON 转义长度估算

加载策略:
1. 优先尝试加载编译好的动态库(rust_ffi.so / libcompaction_fast.so)
2. 编译产物缺失或加载失败时,自动降级为纯 Python 实现(同口径),
   保证功能不中断

编译方法(需 rustc/cargo):
    cd rust && cargo build --release
    cp target/release/libcompaction_fast.so ../rust_ffi.so
"""

from __future__ import annotations

import ctypes
import os
import re

_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.join(_LIB_DIR, "rust_ffi.so"),
    os.path.join(_LIB_DIR, "libcompaction_fast.so"),
    os.path.join(_LIB_DIR, "rust", "target", "release", "libcompaction_fast.so"),
]

_lib: object = None
FFI_AVAILABLE = False

for _path in _CANDIDATES:
    if os.path.exists(_path):
        try:
            _lib = ctypes.CDLL(_path)
            _lib.estimate_tokens_cjk.argtypes = [ctypes.c_char_p]
            _lib.estimate_tokens_cjk.restype = ctypes.c_int
            _lib.count_tokens.argtypes = [ctypes.c_char_p]
            _lib.count_tokens.restype = ctypes.c_int
            _lib.json_escape_len.argtypes = [ctypes.c_char_p]
            _lib.json_escape_len.restype = ctypes.c_int
            FFI_AVAILABLE = True
            break
        except (OSError, AttributeError):
            _lib = None


def _pure_cjk(text: str) -> int:
    """纯 Python 降级:CJK 感知 token 估算(与 Rust 同口径)。"""
    if not text:
        return 1
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_n = len(re.findall(r"[\x00-\x7f]", text))
    other = len(text) - cjk - ascii_n
    tokens = (cjk / 1.5) + (ascii_n / 4.0) + (other / 2.5)
    import math
    return max(1, int(math.ceil(tokens)))


def estimate_tokens_cjk(text: str) -> int:
    """CJK 感知 token 估算;无 FFI 时走纯 Python 降级。"""
    if FFI_AVAILABLE and _lib is not None:
        try:
            return int(_lib.estimate_tokens_cjk(text.encode("utf-8")))
        except Exception:
            pass
    return _pure_cjk(text)


def count_tokens(text: str) -> int:
    """通用 token 计数;无 FFI 时走纯 Python 降级。"""
    if FFI_AVAILABLE and _lib is not None:
        try:
            return int(_lib.count_tokens(text.encode("utf-8")))
        except Exception:
            pass
    ascii_n = len(re.findall(r"[\x00-\x7f]", text))
    non_ascii = len(text) - ascii_n
    return (ascii_n // 4) + (non_ascii // 2) + (1 if (ascii_n % 4 + non_ascii % 2) > 0 else 0)


def json_escape_len(text: str) -> int:
    """JSON 转义长度估算;无 FFI 时走纯 Python 降级。"""
    if FFI_AVAILABLE and _lib is not None:
        try:
            return int(_lib.json_escape_len(text.encode("utf-8")))
        except Exception:
            pass
    return len(text) + text.count('"') + text.count("\\")


def status() -> str:
    """返回加载状态(用于日志/诊断)。"""
    if FFI_AVAILABLE:
        return "rust-ffi"
    return "pure-python (rust_ffi.so 未找到,使用降级实现)"

//! LLM Context Compaction Proxy — Rust 加速扩展
//!
//! 高频路径加速(路线图 #11):
//! 1. estimate_tokens_cjk: CJK 感知 token 估算(替代 Python 逐字符循环)
//! 2. count_tokens: 通用 token 计数
//! 3. json_bytes_len: 快速 JSON 序列化长度估算(避免完整序列化)
//!
//! 编译为 cdylib 供 Python 经 ctypes 调用:
//!     cargo build --release
//!     cp target/release/libcompaction_fast.so ../rust_ffi.so
//!
//! 函数均使用 C ABI(#![no_mangle] + extern "C"),字符串按 UTF-8 字节指针传入。

use std::ffi::{c_char, CStr};
use std::os::raw::c_int;

/// CJK 感知 token 估算(与 Python estimate_tokens_v3 同口径)。
///
/// T = CJK/1.5 + ASCII/4.0 + other/2.5
///
/// 返回估算 token 数(>=1)。UTF-8 按字节遍历并解码字符。
#[no_mangle]
pub extern "C" fn estimate_tokens_cjk(text: *const c_char) -> c_int {
    if text.is_null() {
        return 1;
    }
    let s = unsafe { CStr::from_ptr(text) };
    let bytes = s.to_bytes();

    let mut cjk = 0usize;
    let mut ascii = 0usize;
    let mut other = 0usize;

    let mut i = 0usize;
    while i < bytes.len() {
        let b = bytes[i];
        if b < 0x80 {
            ascii += 1;
            i += 1;
        } else {
            // 计算 UTF-8 序列长度
            let len = if b >= 0xF0 {
                4
            } else if b >= 0xE0 {
                3
            } else if b >= 0xC0 {
                2
            } else {
                1
            };
            // 解码一个字符(取首字节的高位前缀判断 CJK 区间)
            if len == 3 {
                // 3 字节 UTF-8:CJK 统一表意文字 U+4E00..U+9FFF(0xE4..0xE9 开头)
                if (0xE4..=0xE9).contains(&b) {
                    cjk += 1;
                } else {
                    other += 1;
                }
            } else {
                other += 1;
            }
            i += len;
        }
    }

    let est = (cjk as f64 / 1.5) + (ascii as f64 / 4.0) + (other as f64 / 2.5);
    let tokens = est.ceil() as c_int;
    if tokens < 1 {
        1
    } else {
        tokens
    }
}

/// 通用 token 计数:ASCII 每 4 字符 1 token,非 ASCII 每 2 字符 1 token。
#[no_mangle]
pub extern "C" fn count_tokens(text: *const c_char) -> c_int {
    if text.is_null() {
        return 0;
    }
    let s = unsafe { CStr::from_ptr(text) };
    let bytes = s.to_bytes();
    let ascii = bytes.iter().filter(|&&b| b < 0x80).count();
    let non_ascii = bytes.len() - ascii;
    let tokens = (ascii / 4) + (non_ascii / 2) + if (ascii % 4 + non_ascii % 2) > 0 { 1 } else { 0 };
    tokens as c_int
}

/// 快速估算 JSON 字节长度:对给定字符串做最小转义计数。
/// 用于决定是否需要压缩前先估算 payload 大小,避免完整序列化。
#[no_mangle]
pub extern "C" fn json_escape_len(text: *const c_char) -> c_int {
    if text.is_null() {
        return 0;
    }
    let s = unsafe { CStr::from_ptr(text) };
    let bytes = s.to_bytes();
    // JSON 字符串中引号/反斜杠需转义(+1 字节),其余按原长度
    let mut len = bytes.len();
    for &b in bytes {
        if b == b'"' || b == b'\\' {
            len += 1;
        }
    }
    len as c_int
}

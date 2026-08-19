#!/usr/bin/env bash
# ============================================================
# 循环断点续传下载器(download_loop.sh)
# ============================================================
# 代理对长连接限流,故用"短连接分段 + 断点续传"方式:
#   每次 curl 下载 SEGMENT 秒,超时后重连,直到文件达到目标大小。
#
# 用法: bash download_loop.sh <URL> <输出文件> <目标字节数>
# ============================================================
URL="$1"
OUT="$2"
TARGET="$3"
SEGMENT="${SEGMENT:-90}"   # 每段下载秒数(默认90s,避开限流)
LOGFILE="/tmp/dl_segment.log"

mkdir -p "$(dirname "$OUT")"
echo "[$(date +%H:%M:%S)] 开始循环下载: $(basename "$OUT")" >> "$LOGFILE"

while true; do
    CUR=$(stat -c %s "$OUT" 2>/dev/null || echo 0)
    if [ "$CUR" -ge "$TARGET" ]; then
        echo "[$(date +%H:%M:%S)] ✅ 完成: $(basename "$OUT") ($CUR 字节)" >> "$LOGFILE"
        break
    fi
    # 短连接分段下载(断点续传)
    timeout "$SEGMENT" curl -sL -C - -o "$OUT" "$URL" 2>/dev/null
    NEW=$(stat -c %s "$OUT" 2>/dev/null || echo 0)
    GROW=$(( (NEW - CUR) / 1024 / 1024 ))
    echo "[$(date +%H:%M:%S)] 段完成: $NEW / $TARGET 字节 (+${GROW}MB)" >> "$LOGFILE"
    # 若本段无增长,可能被限流,等 5 秒再试
    if [ "$NEW" -le "$CUR" ]; then
        sleep 5
    fi
    sleep 1
done

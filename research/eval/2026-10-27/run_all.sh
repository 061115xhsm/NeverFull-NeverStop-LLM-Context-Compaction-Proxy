#!/bin/bash
set -u
cd /media/qq/文档/neverfull-compaction-proxy
OUT=research/eval/2026-10-27
PY=/home/qq/miniconda3/bin/python3
DATA=benchmark/data/locomo10.json

run() {
  tag="$1"; shift
  echo "[$(date +%H:%M:%S)] START $tag"
  $PY benchmark/memory_eval.py --data "$DATA" --tag "$tag" --outdir "$OUT/raw" \
      --markdown "$OUT/reports/$tag.md" "$@"
  echo "[$(date +%H:%M:%S)] END $tag"
}

run baseline_4x3 --convs 4 --questions 3 --reader llm --strategies full,truncate,llm,v11
run ablate_facts --convs 2 --questions 3 --reader llm --strategies v11,full --ablate facts
run ablate_salient --convs 2 --questions 3 --reader llm --strategies v11,full --ablate salient
run ablate_sections --convs 2 --questions 3 --reader llm --strategies v11,full --ablate sections
run ablate_last_session --convs 2 --questions 3 --reader llm --strategies v11,full --ablate last_session
run reader_pro_2x3 --convs 2 --questions 3 --reader llm --reader-model tierflow_pro --strategies v11,llm,full
echo "[$(date +%H:%M:%S)] START cache_eval"
$PY benchmark/cache_eval.py --outdir "$OUT/raw" --markdown "$OUT/reports/cache_eval.md"
echo "[$(date +%H:%M:%S)] END cache_eval"

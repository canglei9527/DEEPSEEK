#!/system/bin/sh
# dims-verify.sh -- 验证 pldims 长期运行的正确性
# 用法: su -c "sh /data/local/tmp/dims-verify.sh"

D=/data/local/tmp/powerlens
DIMS=$D/dims.tsv
PREV=$D/dims.prev

echo "===== 1. 文件状态 ====="
echo "  dims.tsv:  $(wc -l < $DIMS 2>/dev/null) 行, $(wc -c < $DIMS 2>/dev/null) 字节"
echo "  dims.prev: $(wc -l < $PREV 2>/dev/null) 行"
echo "  dims.tsv 修改时间: $(date -r $DIMS '+%F %T' 2>/dev/null)"
echo "  dims.prev 修改时间: $(date -r $PREV '+%F %T' 2>/dev/null)"
echo "  当前时间: $(date '+%F %T')"

echo ""
echo "===== 2. dims.tsv 里的时间戳（应只有 1 个 = 最新批次）====="
cut -f1 $DIMS 2>/dev/null | sort -u | while read ts; do
  echo "  $ts  = $(date -d @$((ts/1000)) '+%F %T' 2>/dev/null)"
done

echo ""
echo "===== 3. 增量合理性检查 ====="
# 第9列是 intervalMs
INT=$(head -n1 $DIMS 2>/dev/null | cut -f9)
if [ -n "$INT" ] && [ "$INT" -gt 0 ]; then
  echo "  采样间隔: $((INT/1000)) 秒 = $((INT/60000)) 分钟"
  # 检查是否有 Δfg 超过 interval（不可能）
  BAD=$(awk -F'\t' -v i="$INT" '$3 > i*1.05 || $4 > i*1.05 || $5 > i*1.05 {c++} END{print c+0}' $DIMS 2>/dev/null)
  echo "  Δ 超过 interval 的异常行: $BAD"
  if [ "$BAD" -eq 0 ]; then
    echo "  [OK] 增量数据合理"
  else
    echo "  [注意] 有 $BAD 行增量超过采样间隔（可能是多进程累加，属正常）"
  fi
else
  echo "  [异常] 没有 intervalMs 列（可能是旧版 pldims 写的）"
fi

echo ""
echo "===== 4. 各维度统计 ====="
awk -F'\t' '{
  n++; fg+=$3; bg+=$4; fgs+=$5; off+=$6; wl+=$7; wlo+=$8
  if($3>0) nfg++; if($4>0) nbg++; if($5>0) nfgs++
} END {
  printf "  UID 行数: %d\n", n
  printf "  Δfg  总和: %.1f 秒  (非零 UID: %d)\n", fg/1000, nfg
  printf "  Δbg  总和: %.1f 秒  (非零 UID: %d)\n", bg/1000, nbg
  printf "  Δfgs 总和: %.1f 秒  (非零 UID: %d)\n", fgs/1000, nfgs
  printf "  ΔcpuOff:   %.1f 秒\n", off/1000
  printf "  Δwl:       %.1f 秒\n", wl/1000
  printf "  ΔwlOff:    %.1f 秒\n", wlo/1000
}' $DIMS

echo ""
echo "===== 5. 前台时长 top 8（应能反映真实使用）====="
awk -F'\t' '$3>0 {printf "  uid=%-7s Δfg=%.1fs\n", $2, $3/1000}' $DIMS | sort -t= -k3 -nr | head -n 8

echo ""
echo "===== 6. 后台时长 top 8 ====="
awk -F'\t' '$4>0 {printf "  uid=%-7s Δbg=%.1fs\n", $2, $4/1000}' $DIMS | sort -t= -k3 -nr | head -n 8

echo ""
echo "===== 7. 结论 ====="
CT=$(cut -f1 $DIMS 2>/dev/null | sort -u | wc -l)
if [ "$CT" -eq 1 ]; then
  echo "  [OK] dims.tsv 只含最新一批（每次覆盖，正确）"
elif [ "$CT" -eq 0 ]; then
  echo "  [问题] dims.tsv 为空"
else
  echo "  [注意] dims.tsv 含 $CT 个批次（App 只读最新，不影响）"
fi
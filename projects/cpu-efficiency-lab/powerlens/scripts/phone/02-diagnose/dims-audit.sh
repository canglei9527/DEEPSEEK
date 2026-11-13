#!/system/bin/sh
# dims-audit.sh -- 检查 dims.tsv 的维度和是否正确
# 用法: su -c "sh /data/local/tmp/dims-audit.sh"

D=/data/local/tmp/powerlens
DIMS=$D/dims.tsv
SNAP=$D/snapshots.tsv

echo "===== 1. dims 总量 ====="
echo "  行数: $(wc -l < $DIMS)"
echo "  时间戳: $(cut -f1 $DIMS | sort -u | head -n 3 | tr '\n' ' ')"
echo ""
echo "  各维度总和（第3-8列）:"
awk -F'\t' '{
  fg+=$3; bg+=$4; fgs+=$5; off+=$6; wl+=$7; wlo+=$8
} END {
  printf "    fg    = %.1f 小时\n", fg/3600000
  printf "    bg    = %.1f 小时\n", bg/3600000
  printf "    fgs   = %.1f 小时\n", fgs/3600000
  printf "    cpuOff= %.1f 小时\n", off/3600000
  printf "    wl    = %.1f 小时\n", wl/3600000
  printf "    wlOff = %.1f 小时\n", wlo/3600000
}' $DIMS

echo ""
echo "===== 2. bg 非零的 UID（前 10，按 bg 降序）====="
awk -F'\t' '$4>0 {printf "    uid=%-7s bg=%.1fh fg=%.1fh\n", $2, $4/3600000, $3/3600000}' $DIMS | sort -t= -k3 -nr | head -n 10

echo ""
echo "===== 3. fg 非零的 UID（前 10）====="
awk -F'\t' '$3>0 {printf "    uid=%-7s fg=%.1fh bg=%.1fh\n", $2, $3/3600000, $4/3600000}' $DIMS | sort -t= -k3 -nr | head -n 10

echo ""
echo "===== 4. 统计 ====="
echo -n "  bg>0 的 UID 数: "; awk -F'\t' '$4>0' $DIMS | wc -l
echo -n "  fg>0 的 UID 数: "; awk -F'\t' '$3>0' $DIMS | wc -l
echo -n "  fgs>0 的 UID 数: "; awk -F'\t' '$5>0' $DIMS | wc -l

echo ""
echo "===== 5. 两边 UID 交集检查 ====="
# snapshots 最新一批
LATEST=$(awk -F'\t' '$2=="*"{t=$1} END{print t}' $SNAP)
awk -F'\t' -v t="$LATEST" '$1==t && $2!="*" {print $2}' $SNAP | sort -u > /tmp/s_uid.txt
cut -f2 $DIMS | sort -u > /tmp/d_uid.txt
echo "  snapshots 最新批 UID 数: $(wc -l < /tmp/s_uid.txt)"
echo "  dims UID 数:            $(wc -l < /tmp/d_uid.txt)"
S_IN_D=$(comm -12 /tmp/s_uid.txt /tmp/d_uid.txt | wc -l)
echo "  交集（两边都有）:        $S_IN_D"
echo "  只在 snapshots:          $(comm -23 /tmp/s_uid.txt /tmp/d_uid.txt | wc -l)"
echo "  只在 dims:               $(comm -13 /tmp/s_uid.txt /tmp/d_uid.txt | wc -l)"

echo ""
echo "===== 6. 结论 ====="
awk -F'\t' '{ bg+=$4 } END {
  if (bg == 0) print "  [问题] bg 总和为 0 —— dumpsys 没有 bg 数据"
  else printf "  [OK] bg 总和 %.1f 小时\n", bg/3600000
}' $DIMS
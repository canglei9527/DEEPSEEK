#!/system/bin/sh
# dims-repro.sh -- 最小复现：全新 prev 跑一次，立刻人工核对 u0a341 的 fg/bg/fgs
# 用法: su -c "sh /data/local/tmp/dims-repro.sh"

T=/data/local/tmp/drepro
mkdir -p $T
rm -f $T/dims.prev $T/dims.tsv

D=$T/bs.txt
dumpsys batterystats --charged > $D 2>/dev/null

echo "########## dumpsys 里 u0a341 的全部行（含缩进） ##########"
grep -n 'u0a341' $D | while read -r L; do
  echo "  $L" | cut -c1-200
done

echo ""
echo "########## pldims 跑第一次（基线） ##########"
/data/local/tmp/plc/pldims -d $T 2>&1 | sed 's/^/  /'

echo ""
echo "########## 立刻 dumpsys 第二次并打印 prev ##########"
dumpsys batterystats --charged > $D 2>/dev/null
echo "  --- dumpsys 第二次 u0a341 headline ---"
grep -E '^ +UID u0a341:' $D | cut -c1-200 | sed 's/^/  /'
echo "  --- prev 里 10341 ---"
awk -F'\t' '$2==10341 {printf "  fg=%d bg=%d fgs=%d cpuOff=%d wl=%d wlOff=%d\n", $3,$4,$5,$6,$7,$8}' $T/dims.prev

echo ""
echo "########## 换算对照（期望 fg=括号时长） ##########"
python3 - <<'EOF' 2>/dev/null || echo "  (无 python3，人工比对上面两行)"
EOF

echo ""
echo "########## 全表一致性：prev 里 fg 异常大的 UID ##########"
echo "  (fg > 20h = 72000000ms 视为异常，dumpsys 全机 stats 才 6h)"
awk -F'\t' '$3>20000000 {printf "  uid=%-7s fg=%.1fh\n", $2, $3/3600000}' $T/dims.prev | head -n 20

echo ""
echo "########## 统计 ##########"
awk -F'\t' 'BEGIN{n=0} {n++} $3>20000000{b++} $4>20000000{c++} $5>20000000{d++} END{
  printf "  UID 总数=%d  fg>20h: %d  bg>20h: %d  fgs>20h: %d\n", n, b+0, c+0, d+0
}' $T/dims.prev

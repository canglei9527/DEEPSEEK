#!/system/bin/sh
# dims-coverage.sh -- 分析 dims 各维度的覆盖率，找出为什么 mode=2/3 项数太少
# 用法: su -c "sh /data/local/tmp/dims-coverage.sh"

DIMS=/data/local/tmp/powerlens/dims.tsv
SNAP=/data/local/tmp/powerlens/snapshots.tsv

echo "===== 1. dims 基本信息 ====="
echo "  行数: $(wc -l < $DIMS)"
INT=$(head -n1 $DIMS | cut -f9)
echo "  interval: $INT ms = $(awk -v i=$INT 'BEGIN{printf "%.1f", i/60000}') 分钟"

echo ""
echo "===== 2. 各维度非零 UID 数 ====="
awk -F'\t' '
{
  n++
  if ($3 > 0)  fg++
  if ($4 > 0)  bg++
  if ($5 > 0)  fgs++
  if ($6 > 0)  off++
  if ($7 > 0)  wl++
  if ($8 > 0)  wlo++
}
END {
  printf "  总 UID: %d\n", n
  printf "  Δfg  >0: %d\n", fg
  printf "  Δbg  >0: %d\n", bg
  printf "  Δfgs >0: %d\n", fgs
  printf "  ΔcpuOff>0: %d\n", off
  printf "  Δwl  >0: %d\n", wl
  printf "  ΔwlOff>0: %d\n", wlo
}' $DIMS

echo ""
echo "===== 3. 各维度总和 ====="
awk -F'\t' '{
  fg+=$3; bg+=$4; fgs+=$5; off+=$6; wl+=$7; wlo+=$8
} END {
  printf "  Δfg  总和: %.1f 秒\n", fg/1000
  printf "  Δbg  总和: %.1f 秒\n", bg/1000
  printf "  Δfgs 总和: %.1f 秒\n", fgs/1000
  printf "  ΔcpuOff:   %.1f 秒\n", off/1000
  printf "  Δwl:       %.1f 秒\n", wl/1000
  printf "  ΔwlOff:    %.1f 秒\n", wlo/1000
}' $DIMS

echo ""
echo "===== 4. 三者关系检查（Δfg + Δbg 应 ≈ UID 存在时长）====="
awk -F'\t' -v i="$INT" '{
  s = $3 + $4
  if (s > i * 1.1) over++
  if (s > 0 && s < i * 0.5) under++
  n++
} END {
  printf "  Δfg+Δbg 超过 interval*1.1 的: %d / %d\n", over+0, n
  printf "  Δfg+Δbg 不足 interval*0.5 的: %d / %d\n", under+0, n
  if (over+0 > 0) print "    (说明 fg 和 bg 有重叠，dumpsys 语义如此：fg 包含在 bg 里)"
}' $DIMS

echo ""
echo "===== 5. 与 snapshots 的 UID 交集 ====="
LATEST=$(awk -F'\t' '$2=="*"{t=$1} END{print t}' $SNAP)
awk -F'\t' -v t="$LATEST" '$1==t && $2!="*" {print $2}' $SNAP | sort -u > /tmp/sc.txt
cut -f2 $DIMS | sort -u > /tmp/dc.txt
echo "  snapshots 最新批: $(wc -l < /tmp/sc.txt) 个 UID"
echo "  dims:             $(wc -l < /tmp/dc.txt) 个 UID"
echo "  交集:             $(comm -12 /tmp/sc.txt /tmp/dc.txt | wc -l)"

echo ""
echo "===== 6. 为什么 mode=2/3 项数少 ====="
echo "  App 侧逻辑（mode=2 后台）："
echo "    sec = Δbg/1000；若 sec <= 0.001 则跳过"
echo "    weight = √(sec × CPU秒)；若 CPU秒 < 0.01 则用 sec*0.05"
echo "    w <= 0.001 也跳过"
echo ""
echo "  所以「项数少」= 大部分 UID 的 Δbg 是 0，或者 CPU 增量为 0 导致权重过小"
echo ""
echo "  实测：Δbg>0 的 UID 数 = $(awk -F'\t' '$4>0' $DIMS | wc -l)"
echo "        Δfgs>0 的 UID 数 = $(awk -F'\t' '$5>0' $DIMS | wc -l)"
echo ""
echo "  → 这不是 bug：大部分应用在这 2 分钟里后台时长确实是 0"
echo "  → 想看到更多，需要更长的实测窗口（比如让 App 挂后台跑 20 分钟）"

echo ""
echo "===== 7. 抽样：Δbg 最大的 5 个 ====="
awk -F'\t' '$4>0 {printf "  uid=%-7s Δbg=%.1fs Δfg=%.1fs Δfgs=%.1fs\n", $2, $4/1000, $3/1000, $5/1000}' $DIMS \
  | sort -t= -k3 -nr | head -n 5

echo ""
echo "===== 8. 抽样：Δfgs 最大的 5 个 ====="
awk -F'\t' '$5>0 {printf "  uid=%-7s Δfgs=%.1fs Δbg=%.1fs\n", $2, $5/1000, $4/1000}' $DIMS \
  | sort -t= -k3 -nr | head -n 5
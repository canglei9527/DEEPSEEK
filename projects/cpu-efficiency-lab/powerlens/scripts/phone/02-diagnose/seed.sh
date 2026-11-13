#!/system/bin/sh
# seed.sh -- 建立观测基线：连续采 N 次（间隔可选），并打印核对
# 用法: su -c "sh /data/local/tmp/seed.sh [次数] [间隔秒]"
#   默认 6 次，间隔 60 秒（给 UI 一个可算差值的窗口）

N=${1:-6}
GAP=${2:-60}
F=/data/data/com.powerlens.app/files/snapshots.tsv
BIN=/data/local/tmp/plc/plcollect

echo "== 连续采集 $N 次，间隔 ${GAP}s =="
i=1
while [ $i -le $N ]; do
  # 不用 sed 加前缀（busybox sed 对特殊字符转义敏感），直接输出
  echo "--- [$i/$N] ---"
  "$BIN" --once -f "$F" 2>&1
  [ $i -lt $N ] && sleep "$GAP"
  i=$((i+1))
done

echo
echo "== 数据现状 =="
echo "  行数: $(wc -l < $F)"
echo "  --- 最近 6 条电池行（ts / chargeUAh / capacity / status）---"
awk -F'\t' '$2=="*"{print $1, $4, $5, $6}' "$F" | tail -n 6 | sed 's/^/    /'

echo
echo "  --- 问号率 ---"
T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
echo "    $Q / $T"

echo
echo "== 核算 =="
"$BIN" --status -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "== 重启 App 刷新 UI =="
am force-stop com.powerlens.app
sleep 1
am start -n com.powerlens.app/.MainActivity >/dev/null 2>&1
echo "  完成"
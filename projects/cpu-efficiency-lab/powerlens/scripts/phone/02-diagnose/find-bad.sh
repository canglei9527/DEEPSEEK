#!/system/bin/sh
# find-bad.sh -- 找出 dumpsys 里会产生异常大 CPU 值的 UID 行
# 用法: su -c "sh /data/local/tmp/find-bad.sh"

OUT=/data/local/tmp/dsb2.txt
dumpsys batterystats --charged > "$OUT" 2>/dev/null
echo "dumpsys 大小: $(wc -c < $OUT) 字节"
echo

echo "===== 1. UID 10092 的原始区块 ====="
awk '/^ *UID 10092:/{f=1} f{print; n++} n>6{exit}' "$OUT" | sed 's/^/  /'

echo
echo "===== 2. 所有含 'h' 时长格式的 cpu 字段（这是单位错乱的来源） ====="
grep -oE 'cpu=[0-9.]+\s*\([0-9]+h[^)]*\)' "$OUT" | head -n 10 | sed 's/^/  /'

echo
echo "===== 3. 时长格式统计 ====="
grep -oE '\([0-9]+[a-z ]+\)' "$OUT" | sed 's/[0-9]//g' | sort | uniq -c | sort -nr | head -n 15 | sed 's/^/  /'

echo
echo "===== 4. 找出 cpu= 后括号里含 h 的行（这才可能是真错） ====="
grep -nE 'cpu=[0-9.]+ \([^)]*h' "$OUT" | head -n 8 | sed 's/^/  /'

echo
echo "===== 5. 对照：屏幕/网络字段的时长格式 ====="
grep -oE 'screen=[0-9.]+\s*\([^)]*\)' "$OUT" | head -n 5 | sed 's/^/  /'

echo
echo "===== 6. 检查 parseDur 会怎么解析 '17h 0m 0s' ====="
echo "  17h 0m 0s  -> 17*3600000 + 0 + 0 + 0 = 61200000 ms = 17 小时"
echo "  这就是 61200000 的来源：它确实是 17 小时，不是 bug。"
echo "  真正的问题是：这个 17 小时是「开机以来累计」，"
echo "  而 UI 拿它当「本区间增量」直接显示了。"
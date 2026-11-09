#!/system/bin/sh
# pldims-debug.sh -- 追踪 pldims 解析过程，找出 cpuOff/wlOff 为 0 的原因
# 用法: su -c "sh /data/local/tmp/pldims-debug.sh"

echo "===== 1. 计算当前累计值（不存文件）====="
# 直接调用 pldims，看它算出的累计总量
cat > /tmp/t.c <<'CEOF'
CEOF
echo "  (用现有 pldims 的 prev 文件反推)"

PREV=/data/local/tmp/powerlens/dims.prev
if [ -f "$PREV" ]; then
  echo "  prev 文件行数: $(wc -l < $PREV)"
  echo "  --- prev 里 cpuOff 总和 ---"
  awk -F'\t' '{s+=$6} END{printf "    %.1f 秒\n", s/1000}' $PREV
  echo "  --- prev 里 wlOff 总和 ---"
  awk -F'\t' '{s+=$8} END{printf "    %.1f 秒\n", s/1000}' $PREV
  echo "  --- prev 里 cpuOff>0 的 UID 数 ---"
  awk -F'\t' '$6>0' $PREV | wc -l
fi

echo ""
echo "===== 2. 现在再跑一次 pldims，看它有没有把 cpuOff 写进 dims.tsv ====="
/data/local/tmp/plc/pldims 2>&1 | sed 's/^/  /'

DIMS=/data/local/tmp/powerlens/dims.tsv
echo "  --- dims.tsv 里 cpuOff 总和 ---"
awk -F'\t' '{s+=$6} END{printf "    %.1f 秒\n", s/1000}' $DIMS
echo "  --- dims.tsv 里 cpuOff>0 的 UID 数 ---"
awk -F'\t' '$6>0' $DIMS | wc -l

echo ""
echo "===== 3. 检查 dumpsys 里 cpu= 的两种形态 ====="
echo "  --- 形态 A：cpu=数值 (时长)  [UID 段内联格式] ---"
dumpsys batterystats --charged 2>/dev/null | grep -o 'cpu=[0-9.]* ([^)]*)' | head -n 3 | sed 's/^/    /'

echo "  --- 形态 B：cpu: 数值 apps:  [summary 格式] ---"
dumpsys batterystats --charged 2>/dev/null | grep -o 'cpu: [0-9.]* apps: [0-9.]* duration: [^$]*' | head -n 3 | sed 's/^/    /'

echo ""
echo "===== 4. 确认 (on battery, screen off/doze) 段的实际内容 ====="
dumpsys batterystats --charged 2>/dev/null | awk '
/\(on battery, screen off\/doze\)/ { found++; if (found <= 2) { print "  --- 第 " found " 个 off 段 ---"; insec=1; n=0; next } }
insec && n < 2 { print "    " $0; n++ }
insec && n >= 2 { insec=0 }
' | head -n 12

echo ""
echo "===== 5. 结论 ====="
echo "  如果 prev 文件里 cpuOff 总和 > 0，但 dims.tsv 里是 0："
echo "    → 说明增量计算把负数钳成 0 了（counter 重置）"
echo "  如果 prev 文件里 cpuOff 也是 0："
echo "    → 说明解析逻辑根本没匹配到 off 段的 cpu="
echo ""
echo "  实测这台机器亮屏为主，off 段的 cpu 增量可能真的很小（< 1 秒）"
echo "  因为 5 分钟内如果一直亮屏，息屏 CPU 增量就是 0 —— 这是正常的"
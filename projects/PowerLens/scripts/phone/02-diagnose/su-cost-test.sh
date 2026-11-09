#!/system/bin/sh
# su-cost-test.sh -- 测 su 调用的开销，以及 root 脚本能否免 su 直接跑二进制
#
# 背景疑问：
#   守护循环每轮都 `su -c "plcollect ..."`，
#   实测 tick=22 用掉 26 分钟 → 每轮 71.8 秒，比设定的 60 秒多 12 秒。
#   怀疑是 su 本身的开销。
#
#   如果守护脚本已经是 root（通过 su -c "sh daemon.sh" 启动），
#   那它 fork 出的子进程理论上应该也是 root，
#   就不需要每次都 su 了。
#
# 用法: su -c "sh /data/local/tmp/su-cost-test.sh"

BIN=/data/local/tmp/plc/plcollect
TMP=/data/local/tmp/su-test.tsv

echo "===== 0. 当前身份 ====="
id

echo ""
echo "===== 1. 测量 su 调用开销（10 次取平均）====="
i=1
T0=$(date +%s%N 2>/dev/null || echo 0)
while [ $i -le 10 ]; do
  su -c "cat /proc/uptime" >/dev/null 2>&1
  i=$((i+1))
done
T1=$(date +%s%N 2>/dev/null || echo 0)
if [ "$T0" != "0" ] && [ "$T1" != "0" ]; then
  echo "  10 次 su 耗时: $(( (T1-T0)/1000000 )) ms，平均 $(( (T1-T0)/10000000 )) ms/次"
else
  # 老 busybox 没有 %N，用秒
  T0=$(date +%s)
  i=1
  while [ $i -le 10 ]; do su -c "true" >/dev/null 2>&1; i=$((i+1)); done
  T1=$(date +%s)
  echo "  10 次 su 耗时: $((T1-T0)) 秒"
fi

echo ""
echo "===== 2. 直接调用二进制（不套 su）能否成功 ====="
rm -f "$TMP"
OUT=$("$BIN" --once -f "$TMP" 2>&1)
RC=$?
echo "  rc=$RC"
echo "  输出: $OUT"
if [ -f "$TMP" ]; then
  echo "  文件: $(wc -l < $TMP) 行"
  echo "  [OK] 免 su 直接调用成功"
  rm -f "$TMP"
else
  echo "  [FAIL] 免 su 调用失败（子进程丢了 root）"
fi

echo ""
echo "===== 3. 对比：套 su 调用 ====="
rm -f "$TMP"
OUT2=$(su -c "$BIN --once -f $TMP" 2>&1)
echo "  输出: $OUT2"
if [ -f "$TMP" ]; then
  echo "  文件: $(wc -l < $TMP) 行"
  rm -f "$TMP"
fi

echo ""
echo "===== 4. 测量 plcollect 本身的耗时 ====="
S=$(date +%s)
"$BIN" --once -f /data/local/tmp/su-test2.tsv >/dev/null 2>&1
E=$(date +%s)
echo "  直接调用: $((E-S)) 秒（含二进制启动）"

S=$(date +%s)
su -c "$BIN --once -f /data/local/tmp/su-test3.tsv" >/dev/null 2>&1
E=$(date +%s)
echo "  套 su 调用: $((E-S)) 秒"

rm -f /data/local/tmp/su-test2.tsv /data/local/tmp/su-test3.tsv

echo ""
echo "===== 5. 结论指引 ====="
echo "  如果第 2 节能成功（免 su），守护就应改成直接调用，"
echo "  这样每轮开销从 ~12 秒降到 ~0.1 秒，周期能精确到 60 秒。"
echo "  如果第 2 节失败，就必须保留 su，改用「绝对时间调度」而非 tick 计数。"
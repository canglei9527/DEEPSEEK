#!/system/bin/sh
# cmp.sh -- C 版 vs Java/dumpsys 版 实测对比
# 用法: su -c "sh /data/local/tmp/cmp.sh"

BIN=/data/local/tmp/plc/plcollect
OUT=/data/local/tmp/plc/bench.tsv

echo "########## 1. C 版单次采集（跑 10 次取均值） ##########"
rm -f "$OUT"
T0=$(date +%s%N)
i=0
while [ $i -lt 10 ]; do
  $BIN --once -f "$OUT" -q > /dev/null 2>&1
  i=$((i+1))
done
T1=$(date +%s%N)
echo "  $(( (T1-T0) / 10000000 )) ms/次"
echo "  输出行数: $(wc -l < $OUT)"
echo "  单次文件增量: $(wc -c < $OUT) 字节 / 10 次 = $(( $(wc -c < $OUT) / 10 )) 字节"

echo
echo "########## 2. dumpsys 版单次（跑 3 次取均值） ##########"
T0=$(date +%s%N)
i=0
while [ $i -lt 3 ]; do
  dumpsys batterystats --charged > /dev/null 2>&1
  i=$((i+1))
done
T1=$(date +%s%N)
echo "  $(( (T1-T0) / 3000000 )) ms/次"

echo
echo "########## 3. 体积对比 ##########"
echo "  dumpsys --charged : $(su -c 'dumpsys batterystats --charged' 2>/dev/null | wc -c) 字节"
echo "  C 版单次          : $(( $(wc -c < $OUT) / 10 )) 字节"

echo
echo "########## 4. C 版进程开销 ##########"
$BIN --once -f "$OUT" -q > /dev/null 2>&1 &
BGPID=$!
sleep 0.15
if [ -d /proc/$BGPID ]; then
  grep -E 'VmRSS|VmSwap|Threads' /proc/$BGPID/status | sed 's/^/  /'
else
  echo "  进程已退出（说明运行时间 < 150ms）"
fi
wait $BGPID 2>/dev/null

echo
echo "  --- 用 /usr/bin/time 风格：最大 RSS ---"
if [ -x /system/bin/time ]; then
  /system/bin/time -v $BIN --once -f "$OUT" -q 2>&1 | grep -E 'Maximum resident|Elapsed' | sed 's/^/  /'
else
  echo "  (无 /system/bin/time，看下面的手动测量)"
fi

echo
echo "########## 5. C 版 --status 核算 ##########"
$BIN --status -f "$OUT"

echo
echo "########## 6. 对比：真实 snapshots.tsv 的核算 ##########"
$BIN --status -f /data/data/com.powerlens.app/files/snapshots.tsv
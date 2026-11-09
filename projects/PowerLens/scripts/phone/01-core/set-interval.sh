#!/system/bin/sh
# set-interval.sh -- 调整采集间隔（不重启守护会导致双实例，所以要一起做）
# 用法: su -c "sh /data/local/tmp/set-interval.sh [秒数]"
#   推荐：
#     60   调试/建立基线时（每次 151 UID ≈ 12KB，一天 17 MB）
#     300  日常（一天 3.5 MB）
#     600  长期（默认，一天 1.7 MB）

NEW=${1:-60}
D=/data/local/tmp/plc
STORE=/data/local/tmp/powerlens/snapshots.tsv

echo "== 目标间隔: ${NEW}s =="

echo "== 杀旧守护 =="
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed $P"
done
rm -f "$D/daemon.pid"
sleep 2

echo "== 立即采一次（确认路径可用） =="
su -c "$D/plcollect --once -f $STORE" 2>&1 | sed 's/^/  /'
chmod 666 "$STORE" 2>/dev/null

echo "== 启动新间隔 =="
setsid sh "$D/plc-daemon.sh" "$NEW" >/dev/null 2>&1 &
sleep 3
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/  /'

echo
echo "== 预估磁盘占用 =="
awk -v n="$NEW" 'BEGIN{
  per = 12000          # 每次约 12 KB（151 UID × 80 字节）
  day = 86400 / n
  printf "  每天采集 %d 次，约 %.1f MB\n", day, day*per/1048576
  printf "  每小时 %d 个采样点\n", 3600/n
}'

echo
echo "== 当前数据 =="
wc -l "$STORE" | sed 's/^/  /'
chmod 666 "$STORE" 2>/dev/null
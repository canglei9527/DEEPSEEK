#!/system/bin/sh
# 40-plc.sh -- 开机拉起 PowerLens C 采集器守护（root）
# 装到 /data/adb/service.d/40-plc.sh
#
# 说明：
#   采集全部由 root 侧完成，App 不再持有任何后台/root 权限，
#   所以 App 的常驻 CPU ≈ 0，也不会被 HyperOS 冻结后台影响。
#
#   采集间隔 60 秒（而非默认 600）：
#     每批约 12 KB，一天 17 MB，换来每小时 60 个采样点，
#     对「谁在耗电」的归因分析足够细。
#     想省空间就把下面的 INTERVAL 改成 300 或 600。

sleep 30

BIN=/data/local/tmp/plc/plcollect
DAEMON=/data/local/tmp/plc/plc-daemon.sh
LOG=/data/local/tmp/plc/daemon.log
STORE=/data/local/tmp/powerlens/snapshots.tsv
INTERVAL=60

[ -x "$BIN" ] || { echo "$(date '+%F %T') boot: missing $BIN" >> $LOG; exit 0; }
[ -f "$DAEMON" ] || { echo "$(date '+%F %T') boot: missing $DAEMON" >> $LOG; exit 0; }

mkdir -p /data/local/tmp/powerlens 2>/dev/null
chmod 777 /data/local/tmp/powerlens 2>/dev/null
touch "$STORE" 2>/dev/null
chmod 666 "$STORE" 2>/dev/null

# 先杀掉可能残留的旧实例（按 PID，不用 pkill -f 以免匹配到自己的 sh -c）
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
    kill "$P" 2>/dev/null
done

setsid sh "$DAEMON" "$INTERVAL" >/dev/null 2>&1 &
echo "$(date '+%F %T') boot: plc-daemon started pid=$! interval=${INTERVAL}s store=$STORE" >> $LOG
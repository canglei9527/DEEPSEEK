#!/system/bin/sh
# 40-plc.sh -- 开机拉起 PowerLens 采集守护 + Wi-Fi 保活（root）
# 装到 /data/adb/service.d/40-plc.sh
#
# 说明：
#   ① 采集全部由 root 侧完成，App 不再持有任何后台/root 权限，
#      所以 App 的常驻 CPU ≈ 0，也不会被 HyperOS 冻结后台影响。
#   ② Wi-Fi 保活解决「息屏 1-2 分钟后 ping 100% 丢包」的问题：
#      实测热点仍在（PC 侧 clients=1），但手机射频被挂起。
#      手机侧 ping PC 每 15 秒一次，可让网络栈保持活跃。
#
#   采集间隔 60 秒：
#     每批约 12 KB，一天 17 MB，换来每小时 60 个采样点，
#     对「谁在耗电」的归因分析足够细。
#     想省空间就把下面的 INTERVAL 改成 300 或 600。

sleep 30

BIN=/data/local/tmp/plc/plcollect
DIMBIN=/data/local/tmp/plc/pldims
DAEMON=/data/local/tmp/plc/plc-daemon.sh
PINGLOOP=/data/local/tmp/wifi-ping-loop.sh
LOG=/data/local/tmp/plc/daemon.log
STORE=/data/local/tmp/powerlens/snapshots.tsv
DIMS=/data/local/tmp/powerlens/dims.tsv
INTERVAL=60
DIM_INTERVAL=1200

[ -x "$BIN" ] || { echo "$(date '+%F %T') boot: missing $BIN" >> $LOG; exit 0; }
[ -f "$DAEMON" ] || { echo "$(date '+%F %T') boot: missing $DAEMON" >> $LOG; exit 0; }

mkdir -p /data/local/tmp/powerlens 2>/dev/null
chmod 777 /data/local/tmp/powerlens 2>/dev/null
touch "$STORE" "$DIMS" 2>/dev/null
chmod 666 "$STORE" "$DIMS" 2>/dev/null

# ---- Wi-Fi 保活（先起，保证后续测量不被断网打断）----
if [ -f "$PINGLOOP" ]; then
    # 清掉可能残留的旧循环
    for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'wifi-ping-loop' | grep -v grep | awk '{print $1}'); do
        kill -9 "$P" 2>/dev/null
    done
    rm -f /data/local/tmp/keep-wifi.pid
    # Wi-Fi 息屏不休眠
    settings put global wifi_sleep_policy 2 2>/dev/null
    setsid sh "$PINGLOOP" >/dev/null 2>&1 &
    echo "$(date '+%F %T') boot: wifi-ping-loop started pid=$!" >> $LOG
fi

# ---- 采集守护 ----
# 先杀掉可能残留的旧实例（按 PID，不用 pkill -f 以免匹配到自己的 sh -c）
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
    kill "$P" 2>/dev/null
done

setsid sh "$DAEMON" "$INTERVAL" "$DIM_INTERVAL" >/dev/null 2>&1 &
echo "$(date '+%F %T') boot: plc-daemon started pid=$! cpu=${INTERVAL}s dim=${DIM_INTERVAL}s store=$STORE" >> $LOG
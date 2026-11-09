#!/system/bin/sh
# screen-watch.sh -- 亮灭屏事件记录器 + 亮屏时立即采集
#
# 设计要点（为什么不能简单 sleep 5 循环）：
#   本机 82% 的挂起中止来自 [timerfd]（EPOLLWAKEUP 锁）。
#   如果守护进程用固定高频 sleep 循环，会持续产生 timer 唤醒，
#   相当于自己往火里加油。
#   所以用**自适应间隔**：
#     亮屏时 3 s（用户在用，必须及时抓到"停止使用"的时刻）
#     灭屏时 20 s（只需在用户亮屏后 20 s 内发现，代价低）
#   并且用 `sleep` 而不是 busy loop，不持有 wakelock。
#
# 输出：
#   /data/local/tmp/plc/screen.log   每次状态变化一行：<epoch> <ON|OFF> <brightness> <gap_ms>
#   /data/local/tmp/plc/screen.cur   当前状态（单行，供快速读取）
#
# 用法: su -c "setsid sh /data/local/tmp/plc/screen-watch.sh >/dev/null 2>&1 &"

BL=/sys/class/backlight/panel0-backlight/brightness
BLP=/sys/class/backlight/panel0-backlight/bl_power
D=/data/local/tmp/plc
LOG=$D/screen.log
CUR=$D/screen.cur
PLC=$D/plcollect
STORE=/data/data/com.powerlens.app/files/snapshots.tsv

[ -r "$BL" ] || { echo "缺 $BL" > $D/screen.err; exit 1; }
mkdir -p $D

ON_GAP=3
OFF_GAP=20

read_state() {
  b=$(cat $BL 2>/dev/null)
  [ -z "$b" ] && b=0
  p=$(cat $BLP 2>/dev/null)
  [ -z "$p" ] && p=0
  # brightness>0 且 bl_power==0 才算真正亮屏
  if [ "$b" -gt 0 ] 2>/dev/null && [ "$p" -eq 0 ] 2>/dev/null; then
    echo "ON $b"
  else
    echo "OFF $b"
  fi
}

set -- $(read_state)
LAST=$1
LASTB=$2
LASTTS=$(date +%s)

echo "$(date +%s) $LAST $LASTB -" >> $LOG
echo "$LAST $LASTB $(date +%s)" > $CUR

while true; do
  if [ "$LAST" = "ON" ]; then GAP=$ON_GAP; else GAP=$OFF_GAP; fi
  sleep $GAP

  set -- $(read_state)
  NOW=$1
  NOWB=$2

  if [ "$NOW" != "$LAST" ]; then
    TS=$(date +%s)
    GAPMS=$(( (TS - LASTTS) * 1000 ))
    echo "$TS $NOW $NOWB $GAPMS" >> $LOG
    echo "$NOW $NOWB $TS" > $CUR

    # 亮屏 → 立刻采集一次（用户开始用手机，正好是分析"前端耗电"的起点）
    if [ "$NOW" = "ON" ]; then
      [ -x "$PLC" ] && "$PLC" --once -f "$STORE" -q 2>/dev/null
    fi
    LAST=$NOW
    LASTB=$NOWB
    LASTTS=$TS
  fi
done
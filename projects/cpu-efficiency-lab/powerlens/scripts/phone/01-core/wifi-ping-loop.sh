#!/system/bin/sh
# wifi-ping-loop.sh -- 独立的 ping 保活循环（与 keep-wifi.sh 分离，避免多层引号）
#
# 为什么要拆出来：
#   keep-wifi.sh 里用 `setsid sh -c '...一大段...'` 嵌套，
#   经过 su -c 之后引号层级太深，实测脚本内的 ip/ping 会丢权限。
#   拆成独立 .sh 后由 setsid 直接执行，只有一层。
#
# 用法（由 keep-wifi.sh 调用）:
#   su -c "setsid sh /data/local/tmp/wifi-ping-loop.sh >/dev/null 2>&1 &"

LOG=/data/local/tmp/keep-wifi.log
PIDF=/data/local/tmp/keep-wifi.pid

echo $$ > $PIDF
echo "$(date '+%F %T') ping-loop start pid=$$" >> $LOG

# ===== 确定 ping 目标 =====
# 本机热点拓扑：wlan0 192.168.137.22/24，无默认网关
# 目标 = PC 端 192.168.137.1（Windows 移动热点固定 .1）
MYIP=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1)
TARGET=""
if [ -n "$MYIP" ]; then
  CAND=$(echo "$MYIP" | awk -F. '{printf "%s.%s.%s.1", $1, $2, $3}')
  if ping -c 1 -W 2 "$CAND" >/dev/null 2>&1; then
    TARGET="$CAND"
  fi
fi
# 兜底：路由表
if [ -z "$TARGET" ]; then
  HEX=$(awk '$2=="00000000"{print $3}' /proc/net/route 2>/dev/null | head -n1)
  if [ -n "$HEX" ] && [ "$HEX" != "00000000" ]; then
    TARGET=$(printf '%d.%d.%d.%d' \
      $((0x${HEX:6:2})) $((0x${HEX:4:2})) $((0x${HEX:2:2})) $((0x${HEX:0:2})) 2>/dev/null)
  fi
fi

if [ -z "$TARGET" ]; then
  echo "$(date '+%F %T') FATAL: 无 ping 目标 (myip=$MYIP)" >> $LOG
  exit 1
fi

echo "$(date '+%F %T') myip=$MYIP target=$TARGET" >> $LOG

N=0
FAIL=0
while true; do
  if ping -c 1 -W 2 "$TARGET" >/dev/null 2>&1; then
    [ "$FAIL" -gt 0 ] && echo "$(date '+%F %T') recovered after $FAIL fails" >> $LOG
    FAIL=0
  else
    FAIL=$((FAIL+1))
    [ "$FAIL" -eq 10 ] && echo "$(date '+%F %T') WARN: 连续 $FAIL 次失败" >> $LOG
  fi
  N=$((N+1))
  [ $((N % 150)) -eq 0 ] && echo "$(date '+%F %T') alive n=$N fail=$FAIL target=$TARGET" >> $LOG
  sleep 15
done
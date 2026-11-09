#!/system/bin/sh
# restart-v4.sh -- 重启采集守护 v4（双采集器）并验证
# 用法: su -c "sh /data/local/tmp/restart-v4.sh"

D=/data/local/tmp/plc
STORE=/data/local/tmp/powerlens/snapshots.tsv
DIMS=/data/local/tmp/powerlens/dims.tsv

echo "== 杀掉旧守护 =="
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null
  echo "  killed $P"
done
rm -f "$D/daemon.pid"
sleep 2

echo "== 确认组件 =="
ls -l "$D/plcollect" "$D/pldims" "$D/plc-daemon.sh" 2>&1 | sed 's/^/  /'

echo "== 启动 v4（CPU 60s / 维度 1200s）=="
setsid sh "$D/plc-daemon.sh" 60 1200 >/dev/null 2>&1 &
sleep 5

echo "== 实例检查 =="
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/  /'

echo "== 立即各跑一次 =="
su -c "$D/plcollect --once -f $STORE" 2>&1 | sed 's/^/  snap: /'
su -c "$D/pldims -f $DIMS" 2>&1 | sed 's/^/  dims: /'
chmod 666 "$STORE" "$DIMS" 2>/dev/null

echo "== 数据状态 =="
echo "  snapshots: $(wc -l < $STORE 2>/dev/null) 行"
echo "  dims     : $(wc -l < $DIMS 2>/dev/null) 行"
echo "  --- dims 前 5 行（uid fg bg fgs cpuOff wl wlOff）---"
head -n 5 "$DIMS" 2>/dev/null | awk -F'\t' '{printf "    %-7s fg=%-10s bg=%-10s fgs=%-10s off=%-9s wl=%-8s wlOff=%s\n", $2,$3,$4,$5,$6,$7,$8}'

echo "== 蜂巢检查：有多少 UID 有 fg 数据 =="
awk -F'\t' '$3>0' "$DIMS" 2>/dev/null | wc -l | sed 's/^/  fg>0 的 UID 数: /'

echo "== 重启 App =="
am force-stop com.powerlens.app
sleep 1
am start -n com.powerlens.app/.MainActivity >/dev/null 2>&1
echo "  完成"
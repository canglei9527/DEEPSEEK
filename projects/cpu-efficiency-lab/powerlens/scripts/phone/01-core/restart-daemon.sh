#!/system/bin/sh
# restart-daemon.sh -- 确保只有一个 plc-daemon 实例
# 用法: su -c "sh /data/local/tmp/restart-daemon.sh"

D=/data/local/tmp/plc

echo "== 当前实例 =="
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/  /'

echo
echo "== 全部杀掉 =="
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
    kill -9 "$P" 2>/dev/null
    echo "  killed $P"
done
sleep 2

echo
echo "== 确认已清空 =="
LEFT=$(ps -A -o ARGS 2>/dev/null | grep -c 'plc-daemon.sh')
echo "  剩余匹配行: $LEFT"

echo
echo "== 启动唯一实例 =="
setsid sh "$D/plc-daemon.sh" 600 >/dev/null 2>&1 &
sleep 3

echo
echo "== 结果 =="
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/  /'
N=$(ps -A -o ARGS 2>/dev/null | grep -c 'plc-daemon.sh')
echo "  实例数: $N"

if [ "$N" -eq 1 ]; then
    echo "  [OK] 唯一实例"
else
    echo "  [注意] 有 $N 个实例，可能存在重复启动路径（检查 service.d）"
    echo "  --- service.d 里的启动脚本 ---"
    ls -l /data/adb/service.d/ 2>/dev/null | sed 's/^/    /'
fi

echo
echo "== 日志 =="
tail -n 5 "$D/daemon.log" 2>/dev/null | sed 's/^/  /'
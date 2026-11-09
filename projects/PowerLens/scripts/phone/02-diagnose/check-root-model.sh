#!/system/bin/sh
# check-root-model.sh -- 检查"App 需要 root"这个错误架构是否还在
# 用法: su -c "sh /data/local/tmp/check-root-model.sh"

APP=com.powerlens.app
F=/data/data/com.powerlens.app/files/snapshots.tsv
D=/data/local/tmp/plc

echo "########## 1. App 的 uid（重装会变，root 授权会丢）##########"
grep " $APP " /data/system/packages.list 2>/dev/null | awk '{print "  uid=" $2 "  pkg=" $1}'
echo "  --- 当前进程 ---"
ps -A -o PID,ARGS 2>/dev/null | grep "$APP" | grep -v grep | sed 's/^/  /'

echo
echo "########## 2. root 守护状态（这才是该负责采集的）##########"
if ps -A -o ARGS 2>/dev/null | grep -q 'plc-daemon.sh'; then
  echo "  [OK] plc-daemon.sh 在运行"
  ps -A -o PID,ARGS 2>/dev/null | grep plc-daemon.sh | grep -v grep | sed 's/^/    /'
else
  echo "  [缺] plc-daemon.sh 未运行 —— App 拿不到数据是必然的"
fi

echo
echo "########## 3. C 采集器是否可用 ##########"
if [ -x "$D/plcollect" ]; then
  echo "  [OK] $D/plcollect 可执行"
  "$D/plcollect" --once -f /data/local/tmp/plc/_probe.tsv 2>&1 | sed 's/^/    /'
  rm -f /data/local/tmp/plc/_probe.tsv
else
  echo "  [缺] $D/plcollect 不存在或不可执行"
fi

echo
echo "########## 4. 数据文件格式健康度 ##########"
if [ -r "$F" ]; then
  echo "  文件: $F"
  echo "  行数: $(wc -l < $F)"
  echo "  --- 电池行格式分类 ---"
  awk -F'\t' '$2=="*" {
    if ($4 > 100000) print "    OK     µAh格式  charge=" $4 " status=" $6
    else if ($4 > 0) print "    伪数据 mAh当µAh  charge=" $4 " status=" $6
    else             print "    空行   charge=0"
  }' "$F" | sort | uniq -c | sort -nr | head -n 10
  echo "  --- 最后 3 条电池行 ---"
  awk -F'\t' '$2=="*"' "$F" | tail -n 3 | sed 's/^/    /'
else
  echo "  [缺] 数据文件不存在"
fi

echo
echo "########## 5. 权限矩阵验证（App 能读什么）##########"
for f in /sys/class/power_supply/battery/charge_counter \
         /proc/uid_cputime/show_uid_stat \
         /proc/uid_io/stats; do
  printf '  %-46s ' "$f"
  if su -c "test -r $f" 2>/dev/null; then
    echo -n "root可读 "
  else
    echo -n "root不可读 "
  fi
  # 用 app 的 uid 测试
  APPUID=$(grep " $APP " /data/system/packages.list 2>/dev/null | awk '{print $2}')
  if [ -n "$APPUID" ]; then
    if su "$APPUID" -c "test -r $f" 2>/dev/null; then
      echo "App可读"
    else
      echo "App不可读"
    fi
  else
    echo "(app uid未知)"
  fi
done

echo
echo "########## 6. 结论 ##########"
if ps -A -o ARGS 2>/dev/null | grep -q 'plc-daemon.sh'; then
  echo "  [正确架构] root 侧采集，App 只读 —— App 不需要 root"
else
  echo "  [需要修复] 守护没起来。App 拿不到数据不是因为没 root，"
  echo "             是因为守护进程没跑。修复方式："
  echo "             su -c \"setsid sh $D/plc-daemon.sh 600 >/dev/null 2>&1 &\""
fi
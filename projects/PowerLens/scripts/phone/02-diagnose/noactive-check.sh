#!/system/bin/sh
# noactive-check.sh -- NoActive 系统应用冻结现状排查
# 用法: su -c "sh /data/local/tmp/noactive-check.sh"

CFG=/data/system/NoActive_fJIecxAm/config
PKG=cn.myflv.noactive

echo "########## 1. NoActive 模块状态 ##########"
echo "  --- 已安装模块 ---"
ls -d /data/adb/modules/*noactive* /data/adb/modules/*NoActive* 2>/dev/null | sed 's/^/    /'
echo "  --- LSPosed 是否启用 ---"
ls -d /data/adb/modules/zygisk_lsposed 2>/dev/null | sed 's/^/    /'
echo "  --- 应用进程 ---"
ps -A -o PID,ARGS 2>/dev/null | grep -i noactive | grep -v grep | sed 's/^/    /'

echo
echo "########## 2. 配置文件内容 ##########"
for f in MasterConfig.json FreezerConfig.json AdvanceConfig.json BaseConfig.json MemConfig.json; do
  if [ -r "$CFG/$f" ]; then
    echo "  --- $f ---"
    cat "$CFG/$f" 2>/dev/null | sed 's/^/    /'
  fi
done

echo
echo "########## 3. Freezer hook 是否生效（关键） ##########"
echo "  --- 模块日志里的冻结记录 ---"
for f in /data/adb/modules/*noactive*/log* /data/data/$PKG/files/log* /data/local/tmp/noactive*; do
  [ -f "$f" ] && { echo "    文件: $f"; tail -n 15 "$f" 2>/dev/null | sed 's/^/      /'; }
done

echo "  --- logcat 里的 NoActive ---"
logcat -d 2>/dev/null | grep -i noactive | tail -n 15 | sed 's/^/    /'

echo
echo "########## 4. 当前冻结状态（cgroup v2） ##########"
echo "  --- 所有 frozen 的 cgroup ---"
find /sys/fs/cgroup -name 'cgroup.freeze' -type f 2>/dev/null | while read f; do
  v=$(cat "$f" 2>/dev/null)
  [ "$v" = "1" ] && echo "    $f"
done | head -n 30

echo
echo "  --- uid_10284 (com.miui.gallery) 的冻结状态 ---"
cat /sys/fs/cgroup/apps/uid_10284/pid_*/cgroup.freeze 2>/dev/null | sort | uniq -c | sed 's/^/    /'

echo
echo "########## 5. CachedAppOptimizer 对照（系统自带的冻结） ##########"
echo "  --- freezer 开关 ---"
for k in freeze_debounce_timeout default_freezer_timeout use_freezer; do
  printf '    %-28s %s\n' "$k" "$(getprop persist.sys.${k} 2>/dev/null)"
done
echo "  --- 系统 freezer 统计 ---"
dumpsys activity settings 2>/dev/null | grep -iE 'freez|cached_app' | head -n 8 | sed 's/^/    /'

echo
echo "########## 6. 结论 ##########"
FROZEN=$(find /sys/fs/cgroup -name 'cgroup.freeze' -type f 2>/dev/null | while read f; do
  [ "$(cat "$f" 2>/dev/null)" = "1" ] && echo x
done | wc -l)
echo "  当前冻结的 cgroup 数: $FROZEN"
if [ "$FROZEN" -gt 0 ]; then
  echo "  [有冻结活动] 说明 freezer 机制在工作"
else
  echo "  [无冻结] 当前亮屏，属正常（冻结只在息屏时发生）"
fi

echo
echo "  系统应用名单（MasterConfig 里的 systemAppSet）:"
grep -o '"systemAppSet"[^]]*]' "$CFG/MasterConfig.json" 2>/dev/null | sed 's/^/    /'
echo "  用户应用名单（userAppSet）:"
grep -o '"userAppSet"[^]]*]' "$CFG/MasterConfig.json" 2>/dev/null | sed 's/^/    /'
echo "  白名单（whiteUidSet）:"
grep -o '"whiteUidSet"[^]]*]' "$CFG/FreezerConfig.json" 2>/dev/null | sed 's/^/    /'
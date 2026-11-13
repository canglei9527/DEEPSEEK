#!/system/bin/sh
# na-q.sh -- NoActive 精确查询（只输出关键信息，不刷屏）
# 用法: su -c "sh /data/local/tmp/na-q.sh"

export PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/ksu/bin:$PATH

echo "=== 1. NoActive 安装 ==="
pm list packages 2>/dev/null | grep -iE 'noactive|myflv'

echo
echo "=== 2. NoActive 进程 ==="
ps -A -o PID,NAME 2>/dev/null | grep -iE 'noactive|myflv' | grep -v grep
echo "(空=无常驻进程，纯 hook 模块正常)"

echo
echo "=== 3. LSPosed 模块目录 ==="
ls /data/adb/lspd 2>&1 | head -n 5
echo "--- LSPosed 版本 ---"
cat /data/adb/modules/zygisk_lsposed/module.prop 2>/dev/null | grep -E 'version|name'

echo
echo "=== 4. LSPosed 是否加载了 NoActive（查日志） ==="
logcat -d -b all 2>/dev/null | grep -iE 'noactive' | grep -iE 'loaded|injected|hook|scope' | head -n 5
echo "(空=没加载)"

echo
echo "=== 5. 配置目录可读性 ==="
ls -ld /data/system/NoActive_fJIecxAm/config 2>&1
echo "--- MasterConfig（前 30 行） ---"
head -n 30 /data/system/NoActive_fJIecxAm/config/MasterConfig.json 2>&1

echo
echo "=== 6. 系统 freezer 开关（真正在工作的） ==="
echo -n "use_freezer: "
dumpsys device_config get activity_manager_native_boot use_freezer 2>&1 | head -n1
echo -n "freezer_cutoff_adj: "
dumpsys activity settings 2>/dev/null | grep -o 'freezer_cutoff_adj=[0-9]*'

echo
echo "=== 7. 被冻结的进程（用 packages.list 反查） ==="
for f in $(find /sys/fs/cgroup/apps -name 'cgroup.freeze' -type f 2>/dev/null); do
  [ "$(cat $f 2>/dev/null)" = "1" ] || continue
  pid=$(echo "$f" | sed 's#.*/pid_\([0-9]*\)/.*#\1#')
  uid=$(echo "$f" | sed 's#.*/uid_\([0-9]*\)/.*#\1#')
  pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list 2>/dev/null)
  printf '  %-6s uid=%-7s %s\n' "$pid" "$uid" "${pkg:-?}"
done 2>/dev/null | head -n 15
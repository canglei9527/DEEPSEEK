#!/system/bin/sh
# noactive-real.sh -- 查清 NoActive 到底有没有工作
# 用法: su -c "sh /data/local/tmp/noactive-real.sh"

export PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/ksu/bin:$PATH

echo "########## 1. NoActive 应用是否安装 ##########"
pm list packages 2>/dev/null | grep -iE 'noactive|myflv' | sed 's/^/  /'
echo "  --- 路径 ---"
pm path cn.myflv.noactive 2>/dev/null | sed 's/^/  /'

echo
echo "########## 2. LSPosed 是否启用 NoActive ##########"
echo "  --- LSPosed 模块列表（数据库） ---"
for db in /data/adb/lspd/config/modules_config.db /data/adb/lspd/config/modules.db /data/user_de/0/org.lsposed.manager/databases/*; do
  [ -e "$db" ] && echo "    找到: $db"
done
# 尝试用 sqlite3 查
if command -v sqlite3 >/dev/null 2>&1; then
  for db in /data/adb/lspd/config/modules_config.db; do
    [ -f "$db" ] && {
      echo "    --- $db ---"
      sqlite3 "$db" "select * from modules;" 2>&1 | head -n 20 | sed 's/^/      /'
    }
  done
else
  echo "    (无 sqlite3，改用文件检查)"
fi

# 直接看 LSPosed 的模块目录
echo "  --- LSPosed 已加载模块（/data/adb/lspd） ---"
ls -R /data/adb/lspd 2>/dev/null | head -n 30 | sed 's/^/    /'

echo
echo "########## 3. NoActive 进程为何不在 ##########"
echo "  --- 是否有 NoActive 相关进程 ---"
ps -A -o PID,NAME 2>/dev/null | grep -iE 'noactive|myflv' | grep -v grep | sed 's/^/    /'
echo "  (空 = 没有常驻进程，NoActive 是纯 hook 模块，没有进程是正常的)"

echo
echo "  --- system_server 里是否加载了 NoActive 的 hook ---"
logcat -d 2>/dev/null | grep -iE 'noactive|myflv' | grep -iE 'load|hook|inject|xposed' | tail -n 10 | sed 's/^/    /'
echo "  (空 = LSPosed 没有加载它)"

echo
echo "  --- LSPosed 模块加载日志（看 NoActive 在不在） ---"
logcat -d 2>/dev/null | grep -iE 'LSPosed|Xposed' | grep -iE 'load|module' | tail -n 15 | sed 's/^/    /'

echo
echo "########## 4. 配置目录权限问题 ##########"
CFG=/data/system/NoActive_fJIecxAm/config
ls -ld "$CFG" 2>&1 | sed 's/^/  /'
echo "  --- 用 root 直接读（绕过 shell 身份） ---"
for f in MasterConfig.json FreezerConfig.json; do
  echo "    --- $f ---"
  su 0 cat "$CFG/$f" 2>&1 | sed 's/^/      /'
done

echo
echo "########## 5. 系统自带 CachedAppOptimizer 现状（真正在工作的） ##########"
echo "  --- 配置 ---"
dumpsys activity settings 2>/dev/null | grep -iE 'freez|cached' | sed 's/^/    /'
echo "  --- device_config ---"
dumpsys device_config get activity_manager_native_boot use_freezer 2>&1 | sed 's/^/    use_freezer: /'
dumpsys device_config get activity_manager_native_boot freeze_debounce_timeout 2>&1 | sed 's/^/    debounce: /'

echo
echo "########## 6. 当前冻结的 12 个 cgroup 是谁 ##########"
for f in $(find /sys/fs/cgroup/apps -name 'cgroup.freeze' -type f 2>/dev/null); do
  [ "$(cat $f 2>/dev/null)" = "1" ] || continue
  pid=$(echo "$f" | sed 's#.*/pid_\([0-9]*\)/.*#\1#')
  uid=$(echo "$f" | sed 's#.*/uid_\([0-9]*\)/.*#\1#')
  pkg=$(grep -m1 " $uid " /data/system/packages.list 2>/dev/null | awk '{print $1}')
  printf '    uid=%s pid=%s %s\n' "$uid" "$pid" "${pkg:-未知}"
done 2>/dev/null

echo
echo "########## 7. 结论 ##########"
echo ""
echo "  如果第 1-3 节显示 NoActive 没有进程、没有 hook 日志，"
echo "  则说明【NoActive 根本没有在工作】，现在所有冻结都是系统 CachedAppOptimizer 做的。"
echo ""
echo "  这意味着交接文档里的「NoActive 系统应用冻结」任务："
echo "    ① 要么修好 NoActive（需要 LSPosed 启用 + 作用域正确）"
echo "    ② 要么放弃 NoActive，改用 freezer.sh（已验证可用）"
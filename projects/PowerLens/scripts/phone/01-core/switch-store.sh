#!/system/bin/sh
# switch-store.sh -- 把数据存储切到 /data/local/tmp（root 一定可写，App 也能读）
#
# 背景：
#   /data/data/com.powerlens.app/files/ 在 pm install -r 之后
#   出现过 root 也无法写入的情况（SELinux 标签或目录权限被重置）。
#   而 /data/local/tmp/ 是 shell_data_file，权限宽松（777），root 和 App 都能读写。
#
#   App 的 Storage.storeFile() 已经实现了「比较两个位置的行数，选多的那个」，
#   所以只要把数据写到 /data/local/tmp/powerlens/snapshots.tsv，
#   App 会自动切换过去，不需要改代码。
#
# 用法: su -c "sh /data/local/tmp/switch-store.sh"

PKG=com.powerlens.app
MAIN=/data/data/$PKG/files/snapshots.tsv
ALT=/data/local/tmp/powerlens/snapshots.tsv
D=/data/local/tmp/plc

echo "########## 1. 测试两个位置的可写性 ##########"
echo "  --- 主路径 ---"
"$D/plcollect" --once -f "$MAIN" 2>&1 | sed 's/^/    /'
M_OK=$?

echo "  --- 备选路径 ---"
mkdir -p /data/local/tmp/powerlens
chmod 777 /data/local/tmp/powerlens
"$D/plcollect" --once -f "$ALT" 2>&1 | sed 's/^/    /'
A_OK=$?

echo
echo "########## 2. 统计两个位置的行数 ##########"
M_LINES=$(wc -l < "$MAIN" 2>/dev/null || echo 0)
A_LINES=$(wc -l < "$ALT" 2>/dev/null || echo 0)
echo "  主路径 $MAIN: $M_LINES 行"
echo "  备选   $ALT: $A_LINES 行"

echo
echo "########## 3. 决定用哪个 ##########"
if [ "$M_LINES" -gt 100 ]; then
  TARGET="$MAIN"
  echo "  用主路径（已有 $M_LINES 行）"
elif [ "$A_LINES" -gt 0 ]; then
  TARGET="$ALT"
  echo "  用备选路径（已有 $A_LINES 行）"
else
  TARGET="$ALT"
  echo "  主路径不可用，用备选路径"
fi

echo
echo "########## 4. 确保备选路径权限 ##########"
chmod 777 /data/local/tmp/powerlens 2>/dev/null
chmod 666 "$ALT" 2>/dev/null
ls -lZ "$ALT" 2>/dev/null | sed 's/^/  /'

echo
echo "########## 5. 让守护写这个路径 ##########"
# 直接改守护脚本里的 STORE 变量
if [ -f "$D/plc-daemon.sh" ]; then
  sed -i "s#^STORE=.*#STORE=$ALT#" "$D/plc-daemon.sh" 2>/dev/null
  grep '^STORE=' "$D/plc-daemon.sh" | sed 's/^/  /'
fi

# 杀旧守护重启
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null
done
rm -f "$D/daemon.pid"
sleep 2
setsid sh "$D/plc-daemon.sh" 600 >/dev/null 2>&1 &
sleep 3
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/  /'

echo
echo "########## 6. 双写：两个位置都写一份（保险） ##########"
"$D/plcollect" --once -f "$ALT" 2>&1 | sed 's/^/  ALT: /'
if "$D/plcollect" --once -f "$MAIN" 2>&1 | grep -q 'UID'; then
  echo "  MAIN: OK"
else
  echo "  MAIN: 不可用（跳过，只用 ALT）"
fi

echo
echo "########## 7. 最终行数 ##########"
echo "  主路径: $(wc -l < $MAIN 2>/dev/null || echo 0) 行"
echo "  备选  : $(wc -l < $ALT 2>/dev/null || echo 0) 行"

echo
echo "########## 8. 核算（用备选路径） ##########"
"$D/plcollect" --status -f "$ALT" 2>&1 | sed 's/^/  /'

echo
echo "########## 9. 重启 App ##########"
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "  完成"
echo ""
echo "  说明：App 的 storeFile() 会比较两个位置的行数，自动选多的那个，"
echo "        所以不需要重新编译 APK。"
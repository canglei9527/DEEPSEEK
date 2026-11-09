#!/system/bin/sh
# ui-status.sh -- 输出 App 状态栏关键信息（避开 PowerShell 引号地狱）
# 用法: su -c "sh /data/local/tmp/ui-status.sh"

APP=com.powerlens.app
XML=/sdcard/ui_status.xml

# 确保 App 在前台
dumpsys power 2>/dev/null | grep -q 'mWakefulness=Awake' || input keyevent KEYCODE_WAKEUP
sleep 1
am force-stop $APP 2>/dev/null
sleep 1
am start -n $APP/.MainActivity >/dev/null 2>&1
sleep 6

# dump
rm -f $XML
uiautomator dump $XML >/dev/null 2>&1

if [ ! -f "$XML" ]; then
  echo "[FAIL] uiautomator dump 失败"
  exit 1
fi

echo "===== 状态栏 ====="
# 提取包含「区间」或「口径」的那个大文本节点
tr '>' '\n' < $XML | grep -o 'text="[^"]*区间[^"]*"' | head -n1 | sed 's/^text="//; s/"$//' | sed 's/&#10;/\n/g'

echo ""
echo "===== 口径与维度 ====="
tr '>' '\n' < $XML | grep -o 'text="[^"]*口径[^"]*"' | head -n1 | sed 's/^text="//; s/"$//'
tr '>' '\n' < $XML | grep -o 'text="[^"]*维度[^"]*"' | head -n1 | sed 's/^text="//; s/"$//'

echo ""
echo "===== 列表前 5 项的名称 ====="
# 每项的 name 是 TextView，CPU 信息在同一节点
tr '>' '\n' < $XML | grep -o 'text="[^"]*"' | sed 's/^text="//; s/"$//' | grep -v '^$' | grep -vE '^(全部|前台|后台|前台服务|采集|诊断|组播)' | head -n 8

echo ""
echo "===== 进程数 ====="
tr '>' '\n' < $XML | grep -o '[0-9]* 个进程' | head -n1
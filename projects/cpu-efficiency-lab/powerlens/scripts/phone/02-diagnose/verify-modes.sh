#!/system/bin/sh
# verify-modes.sh -- 一次性验证四种口径都能正常显示
# 用法（PC 侧）: 依次 push prefs.xml（mode=N）后执行本脚本
#
# 为什么用 prefs 而不是点击下拉框：
#   实测本机 KernelSU 拒绝了 `input tap` / `input keyevent`
#   （java.lang.SecurityException: INJECT_EVENTS），
#   无法用命令点击 Spinner。所以走 SharedPreferences。

APP=com.powerlens.app
PREFS=/data/data/$APP/shared_prefs/powerlens.xml
XML=/sdcard/mode_check.xml
TMPPREFS=/data/local/tmp/prefs.xml

MODE=${1:-0}
MODE_NAME=""
case "$MODE" in
  0) MODE_NAME="全部" ;;
  1) MODE_NAME="前台" ;;
  2) MODE_NAME="后台" ;;
  3) MODE_NAME="前台服务" ;;
esac

# 生成 prefs
cat > $TMPPREFS <<EOF
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="mcast_on" value="true" />
    <boolean name="hide_agg" value="true" />
    <int name="mode" value="$MODE" />
    <int name="range" value="1" />
</map>
EOF

chmod 644 $TMPPREFS
am force-stop $APP 2>/dev/null
sleep 1
cp -f $TMPPREFS $PREFS
chown $(stat -c '%u' /data/data/$APP):$(stat -c '%u' /data/data/$APP) $PREFS 2>/dev/null
chmod 660 $PREFS 2>/dev/null

am start -n $APP/.MainActivity >/dev/null 2>&1
sleep 7

rm -f $XML
uiautomator dump $XML >/dev/null 2>&1

if [ ! -f "$XML" ]; then
  echo "mode=$MODE ($MODE_NAME): [dump 失败]"
  exit 1
fi

# 提取状态栏
STATUS=$(tr '>' '\n' < $XML | grep -o 'text="[^"]*区间[^"]*"' | head -n1 | sed 's/^text="//; s/"$//')
CNT=$(echo "$STATUS" | grep -o '[0-9]* 个进程')
TIP=$(echo "$STATUS" | sed 's/&#10;/\n/g' | grep -E '口径' | head -n1)
DIMS=$(echo "$STATUS" | sed 's/&#10;/\n/g' | grep -E '维度' | head -n1)

echo "mode=$MODE ($MODE_NAME): $CNT"
echo "  口径: ${TIP:-未显示}"
echo "  维度: ${DIMS:-未显示}"

# 数列表项（带 CPU 字样的节点数）
ITEMS=$(tr '>' '\n' < $XML | grep -c 'CPU .*息屏')
echo "  列表项数: $ITEMS"
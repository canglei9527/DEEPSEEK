#!/system/bin/sh
# sel-mode.sh -- 用键盘事件选择 Spinner 项（比点击坐标可靠）
# 用法: su -c "sh /data/local/tmp/sel-mode.sh [前台|后台|全部|前台服务]"

MODE=${1:-前台}
APP=com.powerlens.app

# 确保 App 在前台
am start -n $APP/.MainActivity >/dev/null 2>&1
sleep 2

# Spinner 位置：第一个 spinner 中心 (161, 847) 实际坐标
# 1544 是 DPAD 导航
echo "== 点击第一个 Spinner =="
input tap 161 847
sleep 1

case "$MODE" in
  前台)      # 第 2 项：下移 1 次
    echo "== 选择「前台」=="
    input keyevent KEYCODE_DPAD_DOWN
    sleep 0.3
    input keyevent KEYCODE_ENTER
    ;;
  后台)      # 第 3 项
    echo "== 选择「后台」=="
    input keyevent KEYCODE_DPAD_DOWN
    sleep 0.2
    input keyevent KEYCODE_DPAD_DOWN
    sleep 0.3
    input keyevent KEYCODE_ENTER
    ;;
  前台服务)  # 第 4 项
    echo "== 选择「前台服务」=="
    input keyevent KEYCODE_DPAD_DOWN
    sleep 0.2
    input keyevent KEYCODE_DPAD_DOWN
    sleep 0.2
    input keyevent KEYCODE_DPAD_DOWN
    sleep 0.3
    input keyevent KEYCODE_ENTER
    ;;
  全部)      # 第 1 项
    echo "== 选择「全部」=="
    input keyevent KEYCODE_DPAD_UP
    sleep 0.3
    input keyevent KEYCODE_ENTER
    ;;
esac

sleep 3
echo "== 当前状态 =="
uiautomator dump /sdcard/sm.xml >/dev/null 2>&1
cat /sdcard/sm.xml | tr '>' '\n' | grep -o 'text="[^"]*口径[^"]*"' | head -n1
cat /sdcard/sm.xml | tr '>' '\n' | grep -o 'text="[^"]*维度数据[^"]*"' | head -n1
#!/system/bin/sh
# scrub.sh -- 找最便宜的亮灭屏信号源，并测每次读取成本
# 用法: su -c "sh /data/local/tmp/scrub.sh"

echo "########## 1. backlight 节点 ##########"
for d in /sys/class/backlight/*/; do
  echo "  [$d]"
  for f in brightness actual_brightness max_brightness bl_power; do
    [ -r "$d$f" ] && printf '    %-18s %s\n' "$f" "$(cat $d$f 2>/dev/null)"
  done
done

echo
echo "########## 2. fb / drm ##########"
for f in /sys/class/graphics/fb0/blank /sys/class/graphics/fb0/state \
         /sys/class/drm/card0-DSI-1/status /sys/class/drm/card0-DSI-1/enabled; do
  [ -e "$f" ] && printf '  %-42s %s\n' "$f" "$(cat $f 2>/dev/null)"
done

echo
echo "########## 3. leds ##########"
ls /sys/class/leds/ 2>/dev/null | head -n 20 | sed 's/^/  /'

echo
echo "########## 4. 单次读取成本（各 30 次平均） ##########"
bench() {
  n=$1; cmd=$2
  t0=$(date +%s%N 2>/dev/null || date +%s)
  i=0
  while [ $i -lt 30 ]; do sh -c "$cmd" >/dev/null 2>&1; i=$((i+1)); done
  t1=$(date +%s%N 2>/dev/null || date +%s)
  # 毫秒
  case "$t0" in *[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) echo "  $(( (t1-t0)/30000000 )) ms/次 : $cmd" ;; *) echo "  (无法纳秒计时)" ;; esac
}
BL=$(ls /sys/class/backlight/*/brightness 2>/dev/null | head -n1)
echo "  backlight 节点: ${BL:-无}"
[ -n "$BL" ] && bench x "cat $BL"
bench x "cat /sys/class/graphics/fb0/blank"

echo
echo "  --- dumpsys power（对照，很贵） ---"
t0=$(date +%s)
i=0
while [ $i -lt 3 ]; do dumpsys power >/dev/null 2>&1; i=$((i+1)); done
t1=$(date +%s)
echo "  $(( (t1-t0)*1000/3 )) ms/次"

echo
echo "########## 5. 当前屏幕状态（三种方式对照） ##########"
echo "  dumpsys power : $(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"
[ -n "$BL" ] && echo "  backlight     : $(cat $BL 2>/dev/null)"
echo "  fb0 blank     : $(cat /sys/class/graphics/fb0/blank 2>/dev/null)"

echo
echo "########## 6. 试着用 root 关屏/亮屏（验证可控性） ##########"
echo "  --- 关屏 (KEYCODE_SLEEP=223) ---"
input keyevent 223 2>&1 | head -n 2
sleep 1
echo "    mWakefulness=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"
[ -n "$BL" ] && echo "    backlight=$(cat $BL 2>/dev/null)"

echo "  --- 亮屏 (KEYCODE_WAKEUP=224) ---"
input keyevent 224 2>&1 | head -n 2
sleep 2
echo "    mWakefulness=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"
[ -n "$BL" ] && echo "    backlight=$(cat $BL 2>/dev/null)"

echo
echo "########## 7. settings 里的屏幕状态 ##########"
settings get secure screen_off_timeout 2>/dev/null | sed 's/^/  screen_off_timeout=/'
dumpsys display 2>/dev/null | grep -E 'mScreenState|mState=' | head -n 3 | sed 's/^/  /'
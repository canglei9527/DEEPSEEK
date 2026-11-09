#!/system/bin/sh
# screen-on-fix.sh -- 亮屏静止功耗优化（目标 < 0.5W / 130 mA）
#
# 依据实测（2026-11-09 15:40）：
#   logd 内核态 CPU = 216830 ms，是用户态(60110ms)的 3.6 倍
#   5 秒产生 718 行日志（2.4 行/秒）
#   主因：MiSensorServiceImpl 每 200ms 打印 LightFovOptimizationSensor::screen_angle
#         每次 11 个浮点数（约 90 字节）
#         → 2131 条/分钟 × 90B = 190 KB/分钟 持续写日志
#
# 本脚本做四件事（全部可回滚）：
#   1. 降低 logd buffer 与关闭不值得的 buffer（kernel 已空、crash 很少）
#   2. 关掉 sensor 的持续上报（屏幕角度优化对静止亮屏没用）
#   3. 关掉自动刷新率切换（LTPO 在静止时本应降到 1Hz，若反复切换反而费电）
#   4. 记录基线，供后续对比
#
# 用法:
#   su -c "sh /data/local/tmp/screen-on-fix.sh baseline"   只记录基线
#   su -c "sh /data/local/tmp/screen-on-fix.sh apply"      应用优化
#   su -c "sh /data/local/tmp/screen-on-fix.sh revert"     回滚
#   su -c "sh /data/local/tmp/screen-on-fix.sh status"     查看当前状态

BACKUP=/data/local/tmp/screen-on-fix.backup
ACTION=${1:-status}

measure() {
  # 30 秒电流测量
  C0=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
  T0=$(date +%s)
  sleep 30
  C1=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
  T1=$(date +%s)
  DT=$((T1-T0)); [ $DT -lt 1 ] && DT=30
  awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{
    ma=(a-b)/1000*3600/t
    printf "  电流 %.1f mA = %.2f W (@3.85V)\n", ma, ma*3.85/1000
  }'
}

case "$ACTION" in

baseline)
  echo "===== 基线测量（当前状态，30 秒）====="
  echo "  屏幕: $(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"
  echo "  背光: $(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null)"
  echo "  刷新率: $(dumpsys display 2>/dev/null | grep -o 'mRefreshRate=[0-9.]*' | head -n1)"
  measure
  echo ""
  echo "  --- logd CPU（10 秒增量）---"
  P=$(pidof logd)
  if [ -n "$P" ]; then
    A=$(awk '{print $14+$15}' /proc/$P/stat)
    sleep 10
    B=$(awk '{print $14+$15}' /proc/$P/stat)
    echo "    +$(( (B-A)*10 )) ms / 10s"
  fi
  echo ""
  echo "  --- 日志速率（3 秒）---"
  L0=$(logcat -b main -d 2>/dev/null | wc -l)
  sleep 3
  L1=$(logcat -b main -d 2>/dev/null | wc -l)
  echo "    buffer 行数 $L0 -> $L1"
  ;;

apply)
  echo "===== 应用优化 ====="

  # --- 先记录原始状态（只记一次）---
  if [ ! -f "$BACKUP" ]; then
    {
      echo "logd_size_main=$(logcat -b main -g 2>/dev/null | grep -oE '[0-9]+[KMG]iB' | head -n1)"
      echo "screen_off_timeout=$(settings get system screen_off_timeout)"
      echo "peak_refresh_rate=$(settings get system peak_refresh_rate)"
      echo "min_refresh_rate=$(settings get system min_refresh_rate)"
      echo "user_refresh_rate=$(settings get system user_refresh_rate)"
      echo "smooth_display=$(settings get system miui_smooth_display)"
      echo "backlight=$(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null)"
    } > "$BACKUP"
    echo "  已记录原始状态到 $BACKUP"
    cat "$BACKUP" | sed 's/^/    /'
  else
    echo "  已有备份，跳过记录"
  fi

  # --- 1. 降低日志量 ---
  echo ""
  echo "  [1] 降低日志 buffer"
  # kernel buffer 已空，crash 很少，缩到最小
  logcat -b kernel -G 64K 2>/dev/null && echo "    kernel buffer -> 64K"
  logcat -b crash -G 128K 2>/dev/null && echo "    crash buffer -> 128K"
  # main/system 从 2MiB 降到 1MiB（足够排查，减少 flush 压力）
  logcat -b main -G 1M 2>/dev/null && echo "    main buffer -> 1M"
  logcat -b system -G 1M 2>/dev/null && echo "    system buffer -> 1M"

  # --- 2. 关掉传感器持续上报（screen_angle 200ms 一条的元凶）---
  echo ""
  echo "  [2] 关闭传感器持续优化"
  # 小米的「屏幕角度优化」（LightFovOptimizationSensor）
  settings put system miui_sensor_optimize 0 2>/dev/null
  settings put secure miui_screen_angle_enabled 0 2>/dev/null
  # 「智能感知」「抬起亮屏」等持续传感器
  settings put system raise_to_wake 0 2>/dev/null
  settings put system gesture_wakeup 0 2>/dev/null
  echo "    已关闭：miui_sensor_optimize / screen_angle / raise_to_wake"

  # --- 3. 固定刷新率（避免 LTPO 反复切换）---
  echo ""
  echo "  [3] 固定刷新率 60Hz（静止亮屏够用，省 GPU/显示功耗）"
  settings put system peak_refresh_rate 60 2>/dev/null
  settings put system min_refresh_rate 60 2>/dev/null
  echo "    peak/min -> 60"

  # --- 4. 降低背光（如果当前很高）---
  echo ""
  echo "  [4] 背光检查"
  BL=$(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null)
  MAX=$(cat /sys/class/backlight/panel0-backlight/max_brightness 2>/dev/null)
  echo "    当前 $BL / 最大 $MAX"
  if [ -n "$BL" ] && [ -n "$MAX" ] && [ "$MAX" -gt 0 ]; then
    PCT=$((BL * 100 / MAX))
    echo "    约 $PCT%"
    if [ "$PCT" -gt 60 ]; then
      TARGET=$((MAX * 50 / 100))
      echo "$TARGET" | su -c tee /sys/class/backlight/panel0-backlight/brightness >/dev/null 2>&1
      echo "    已降到 50%（$TARGET）"
    fi
  fi

  echo ""
  echo "===== 优化后的测量（等 20 秒稳定）====="
  sleep 20
  measure
  ;;

revert)
  echo "===== 回滚 ====="
  if [ -f "$BACKUP" ]; then
    . "$BACKUP"
    settings put system peak_refresh_rate "${peak_refresh_rate:-0}" 2>/dev/null
    settings put system min_refresh_rate "${min_refresh_rate:-0}" 2>/dev/null
    settings put system screen_off_timeout "${screen_off_timeout:-600000}" 2>/dev/null
    settings put system miui_sensor_optimize 1 2>/dev/null
    settings put secure miui_screen_angle_enabled 1 2>/dev/null
    settings put system raise_to_wake 1 2>/dev/null
    logcat -b main -G 2M 2>/dev/null
    logcat -b system -G 2M 2>/dev/null
    logcat -b kernel -G 2M 2>/dev/null
    logcat -b crash -G 2M 2>/dev/null
    echo "  已恢复原始设置"
  else
    echo "  [无备份] 只能恢复默认值"
    settings put system peak_refresh_rate 0 2>/dev/null
    settings put system min_refresh_rate 0 2>/dev/null
    logcat -b main -G 2M 2>/dev/null
  fi
  ;;

status)
  echo "===== 当前状态 ====="
  echo "  背光: $(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null) / $(cat /sys/class/backlight/panel0-backlight/max_brightness 2>/dev/null)"
  echo "  peak_refresh_rate: $(settings get system peak_refresh_rate)"
  echo "  min_refresh_rate:  $(settings get system min_refresh_rate)"
  echo "  sensor_optimize:   $(settings get system miui_sensor_optimize)"
  echo ""
  echo "  --- log buffer 大小 ---"
  logcat -g 2>&1 | head -n 5 | sed 's/^/    /'
  echo ""
  echo "  --- logd CPU 10 秒增量 ---"
  P=$(pidof logd)
  if [ -n "$P" ]; then
    A=$(awk '{print $14+$15}' /proc/$P/stat)
    sleep 10
    B=$(awk '{print $14+$15}' /proc/$P/stat)
    echo "    +$(( (B-A)*10 )) ms / 10s"
  fi
  ;;
esac
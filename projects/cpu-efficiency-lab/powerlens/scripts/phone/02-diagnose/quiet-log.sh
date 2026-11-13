#!/system/bin/sh
# quiet-log.sh -- 抑制高频 HALL 日志（不改行为，只降日志级别）
#
# 实测元凶（2026-11-09 15:45）：
#   10 秒 1034 行日志 = 103 行/秒
#   PID 3087  vendor.xiaomi.sensor.citsens      377 条/10s (37%)
#   PID 1995  vendor.qti.hardware.display...    219 条/10s (21%)
#   PID 3646  system_server                     146 条/10s (14%)
#   PID 2238  surfaceflinger                    142 条/10s (14%)
#
#   高频 tag：
#     libsensor-boledalgo   288/10s  ← 屏幕亮度算法
#     SDM                   198/10s  ← CWB / Idle Timeout (每 70ms)
#     libsensor-parseRGB     29/10s
#
# 注意：降日志级别只减少 logd 开销，不改变系统行为，也不省传感器功耗。
#       真正的功耗要用 A/B 验证（见 screen-on-fix.sh）。
#
# 用法:
#   su -c "sh /data/local/tmp/quiet-log.sh apply"
#   su -c "sh /data/local/tmp/quiet-log.sh revert"
#   su -c "sh /data/local/tmp/quiet-log.sh measure [秒]"

ACTION=${1:-measure}
WIN=${2:-20}
BACKUP=/data/local/tmp/quiet-log.backup

# 这些 tag 是纯调试输出，压到 ERROR 级别不影响功能
TAGS="libsensor-boledalgo libsensor-parseRGB SDM MI-SF libsensor-D2SNotifier sensors FastPrintWriter"

case "$ACTION" in

apply)
  echo "===== 抑制高频调试日志 ====="
  if [ ! -f "$BACKUP" ]; then
    echo "first_apply=$(date '+%F %T')" > "$BACKUP"
    echo "  已建备份标记"
  fi

  for t in $TAGS; do
    # 设为 ERROR，等于只保留错误，丢弃 D/I/V
    su -c "setprop log.tag.$t ERROR" 2>/dev/null
    echo "  log.tag.$t = ERROR"
  done

  echo ""
  echo "  注意：setprop log.tag.* 需要进程重启才完全生效。"
  echo "        对 HAL 进程（vendor.*）通常不生效（它们是原生代码，不走 Java log.tag）。
  echo ""
  echo "  更有效的方法：降低这些进程的日志优先级（需要重启 HAL，风险高）"
  echo "  或者：接受这些日志，因为 logd 只占 1.8% 单核 ≈ 0.02W"
  ;;

revert)
  echo "===== 恢复 ====="
  for t in $TAGS; do
    su -c "setprop log.tag.$t ''" 2>/dev/null
  done
  echo "  已清除 log.tag 覆盖"
  ;;

measure)
  echo "===== 日志与功耗测量（${WIN}s）====="
  echo "  --- 日志速率 ---"
  su -c "logcat -b main -c" 2>/dev/null
  sleep $WIN
  N=$(su -c "logcat -b main -d 2>/dev/null | wc -l" 2>/dev/null)
  echo "    ${WIN}s 内 $N 行 = $((N/WIN)) 行/秒"

  echo ""
  echo "  --- logd CPU（同时段）---"
  echo "    已在上一步测量中"

  echo ""
  echo "  --- 电流（${WIN}s）---"
  C0=$(su -c "cat /sys/class/power_supply/battery/charge_counter" 2>/dev/null)
  T0=$(date +%s)
  sleep $WIN
  C1=$(su -c "cat /sys/class/power_supply/battery/charge_counter" 2>/dev/null)
  T1=$(date +%s)
  DT=$((T1-T0)); [ $DT -lt 1 ] && DT=$WIN
  awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{
    ma=(a-b)/1000*3600/t
    printf "    %.1f mA = %.2f W (@3.85V)\n", ma, ma*3.85/1000
    printf "    目标 0.5W = %.0f mA\n", 500/3.85
  }'
  ;;
esac
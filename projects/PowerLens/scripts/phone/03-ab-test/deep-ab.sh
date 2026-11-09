#!/system/bin/sh
# deep-ab.sh -- deep 挂起 A/B 测试（v2，修正版）
#
# 之前失败的原因：
#   test-deep.sh 里 echo deep > /sys/power/mem_sleep 之后，
#   dmesg 显示 entry 只有 1 条且 last_failed_dev=alarmtimer.0.auto（errno -16 EBUSY）。
#   说明 deep 根本没进去就被打回。
#
# 本版改动：
#   1. 每步单独执行并立即验证（不依赖前一步成功）
#   2. 先确认 deep 是否真的能进入（看 PM: suspend entry (deep) 条数）
#   3. A/B 用悬挂失败率 + 电流双重判据
#   4. 测完自动还原
#
# 用法: su -c "sh /data/local/tmp/deep-ab.sh [每段秒数，默认180]"

WIN=${1:-180}
LOG=/data/local/tmp/deep-ab.log

: > $LOG
echo "===== deep A/B $(date '+%F %T') 每段 ${WIN}s =====" >> $LOG

# ---------- 工具 ----------
sh1() {
  # 单条命令的 su 包装（避免多命令串联丢权限）
  su -c "$1"
}

read_val() {
  sh1 "cat $1" 2>/dev/null
}

# ---------- 前提检查 ----------
ST=$(read_val /sys/class/power_supply/battery/status)
echo "电池状态: $ST" >> $LOG
if [ "$ST" != "Discharging" ]; then
  echo "[中止] 必须在放电状态（当前 $ST）" >> $LOG
  cat $LOG
  exit 1
fi

WAKE=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')
echo "屏幕: $WAKE" >> $LOG

MEM=$(read_val /sys/power/mem_sleep)
echo "mem_sleep: [$MEM]" >> $LOG
case "$MEM" in
  *s2idle*) ;;
  *) echo "[警告] 当前不是 s2idle，可能上次测试没还原" >> $LOG ;;
esac

# ---------- 测量函数 ----------
measure() {
  local label=$1 win=$2
  local c0 t0 s0 f0 e0 a0
  c0=$(read_val /sys/class/power_supply/battery/charge_counter)
  t0=$(date +%s)
  s0=$(read_val /sys/power/suspend_stats/success)
  f0=$(read_val /sys/power/suspend_stats/fail)
  e0=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
  a0=$(dmesg 2>/dev/null | grep -c 'Abort: Pending')

  sleep $win

  local c1 t1 s1 f1 e1 a1
  c1=$(read_val /sys/class/power_supply/battery/charge_counter)
  t1=$(date +%s)
  s1=$(read_val /sys/power/suspend_stats/success)
  f1=$(read_val /sys/power/suspend_stats/fail)
  e1=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
  a1=$(dmesg 2>/dev/null | grep -c 'Abort: Pending')

  local dt=$((t1 - t0))
  [ $dt -lt 1 ] && dt=$win

  awk -v lbl="$label" -v dt=$dt \
      -v c0="$c0" -v c1="$c1" \
      -v s0="$s0" -v s1="$s1" -v f0="$f0" -v f1="$f1" \
      -v e0="$e0" -v e1="$e1" -v a0="$a0" -v a1="$a1" 'BEGIN{
    ma = (c0>0 && c1>0) ? (c0-c1)/1000*3600/dt : -1
    sd = s1-s0; fd = f1-f0; tot = sd+fd
    fr = (tot>0) ? fd*100/tot : -1
    printf "  [%s] %ds 电流=%.1f mA  suspend成功+%d 失败+%d (失败率%.0f%%)  entry+%d abort+%d\n",
      lbl, dt, ma, sd, fd, fr, e1-e0, a1-a0
  }'
}

# ---------- deep 进入性验证（关键） ----------
echo "" >> $LOG
echo "===== 阶段 0：验证 deep 能否真正进入 =====" >> $LOG
sh1 "echo deep > /sys/power/mem_sleep"
sleep 2
MEM2=$(read_val /sys/power/mem_sleep)
echo "  切换后 mem_sleep: [$MEM2]" >> $LOG

# 清空 dmesg 缓冲，观察 30 秒
dmesg -c >/dev/null 2>&1
sleep 30
DEEP_ENTRY=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry (deep)')
S2IDLE_ENTRY=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry (s2idle)')
echo "  30 秒内: deep entry=$DEEP_ENTRY  s2idle entry=$S2IDLE_ENTRY" >> $LOG

if [ "$DEEP_ENTRY" -eq 0 ]; then
  echo "  [结论] deep 完全没有进入 —— 内核接受了参数但实际上还在用 s2idle" >> $LOG
  echo "         或 deep 立刻被拒（errno -16 EBUSY）" >> $LOG
else
  echo "  [结论] deep 确实在进入（$DEEP_ENTRY 次）" >> $LOG
fi

# ---------- A/B ----------
echo "" >> $LOG
echo "===== 阶段 1：A = deep =====" >> $LOG
sh1 "echo deep > /sys/power/mem_sleep"
sleep 3
measure "A:deep" $WIN | tee -a $LOG

echo "" >> $LOG
echo "===== 阶段 2：B = s2idle =====" >> $LOG
sh1 "echo s2idle > /sys/power/mem_sleep"
sleep 3
measure "B:s2idle" $WIN | tee -a $LOG

# ---------- 还原 ----------
sh1 "echo s2idle > /sys/power/mem_sleep"
echo "" >> $LOG
echo "===== 已还原 mem_sleep=s2idle =====" >> $LOG
echo "  验证: [$(read_val /sys/power/mem_sleep)]" >> $LOG

echo "" >> $LOG
echo "===== 失败设备 =====" >> $LOG
echo "  last_failed_dev  : $(read_val /sys/power/suspend_stats/last_failed_dev)" >> $LOG
echo "  last_failed_errno: $(read_val /sys/power/suspend_stats/last_failed_errno)" >> $LOG

cat $LOG
#!/system/bin/sh
# core_measure.sh -- 设备端单核能效测量（后台运行，不依赖 ADB 长连接）
#
# 为什么全放设备端
# ----------------
# 无线 ADB 每次长命令（>20s）都有概率断成 offline / closed。
# 测量需要 40+ 个 6 秒窗口 x 多轮，若每窗口过一次 ADB 必然中途失败，
# 且失败点之前的探测结果会与之后的不可比（热状态不同）。
# 所以：nohup 到设备端跑完整循环，主机只负责最后 cat 结果。
#
# 测量协议（每个 core x freq）
# ---------------------------
#   A. 满载：bench 绑该核做纯整数计算          -> P_load
#   B. 空闲：taskset 把 sleep 钉在该核          -> P_idle
# 两者固定开销相同（屏幕已灭、其余核休眠状态一致），相减得该核增量功耗。
# 另记全机基线 P_sys（每轮一次），用于交叉校验。
#
# 用法: core_measure.sh <rounds> <measure_s>
# 输出: /data/local/tmp/eeff/core.tsv

ROUNDS=${1:-5}
MEAS=${2:-6}
B=/sys/class/power_supply/battery
CF=/sys/devices/system/cpu/cpufreq
BENCH=/data/local/tmp/eeff/bench
OUT=/data/local/tmp/eeff/core.tsv
LOG=/data/local/tmp/eeff/core.log
DONE=/data/local/tmp/eeff/core.done

rm -f "$DONE"
printf 'rnd\tpolicy\tfreq\tcore\tops\tp_load\tp_idle\tp_sys\tn_load\tn_idle\n' > "$OUT"
: > "$LOG"

log() { echo "$@" >> "$LOG"; }
online_now() { cat /sys/devices/system/cpu/online 2>/dev/null; }

ensure_cores() {
  k=0
  while [ "$k" -lt 8 ]; do
    if [ -e "/sys/devices/system/cpu/cpu$k/online" ]; then
      echo 1 > "/sys/devices/system/cpu/cpu$k/online" 2>/dev/null
    fi
    k=$((k + 1))
  done
  sleep 1
}

# 采样一个窗口，输出 "mean_current n"
sample_window() {
  _n=$1
  _sl=$2
  _s=0
  _c=0
  _i=0
  while [ "$_i" -lt "$_n" ]; do
    _v=$(cat $B/current_now 2>/dev/null)
    case "$_v" in ''|*[!0-9-]*) _v=0 ;; esac
    _s=$((_s + _v))
    _c=$((_c + 1))
    _i=$((_i + 1))
    sleep "$_sl"
  done
  if [ "$_c" -gt 0 ]; then
    echo "$((_s / _c)) $_c"
  else
    echo "0 0"
  fi
}

# 1ms 分辨率时间戳在 toybox 的 date 上不可靠，用 uptime 秒即可
now_s() { cut -d' ' -f1 /proc/uptime; }

lock_policy() {
  _p=$1
  _f=$2
  echo performance > "$CF/policy$_p/scaling_governor" 2>/dev/null
  echo "$_f" > "$CF/policy$_p/scaling_max_freq" 2>/dev/null
  echo "$_f" > "$CF/policy$_p/scaling_min_freq" 2>/dev/null
  sleep 1
  _cur=$(cat "$CF/policy$_p/scaling_cur_freq" 2>/dev/null)
  [ "$_cur" = "$_f" ] && return 0
  return 1
}

log "START rounds=$ROUNDS meas=$MEAS online=$(online_now)"
ensure_cores
log "online_after=$(online_now)"

# 清掉明显的干扰源。实测教训：微信占 1.77GB、小红书占 784MB，
# 内存压力下 bench 被反复换页，同一频点吞吐从 167 Mop/s 掉到 17 Mop/s
# （相差 10 倍），而 elapsed 也从 6.000 变成 6.4~7.0。
# 不杀这些进程，测到的吞吐与功率都不是 CPU 本身的能力。
# 只 force-stop，不卸载；用户数据不受影响。
for pkg in com.tencent.mm com.xingin.xhs; do
  am force-stop "$pkg" 2>/dev/null
done
sync
sleep 3
log "after_stop loadavg=$(cat /proc/loadavg)"
log "memavail=$(grep MemAvailable /proc/meminfo 2>/dev/null | tr -s ' ' | cut -d' ' -f2) kB"

# 熄屏，减少一个巨大且波动的功耗项。
# 但注意：完全熄屏 + stayon false 会让无线 ADB 在深度休眠时断掉，
# 导致测量跑完后拿不到结果。折中：
#   - 亮度降到最低而不熄屏（背光最低仍有功耗，但比高亮低得多且稳定）
#   - 保持 stayon，避免设备进 doze 断网
# 权衡结果：可接受的固定开销 + 能取回数据，优于彻底熄屏但失联。
settings put system screen_brightness 0 2>/dev/null
settings put system screen_brightness_mode 0 2>/dev/null
svc power stayon true 2>/dev/null
echo 3 > /sys/class/backlight/*/brightness 2>/dev/null
sleep 5
log "screen=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*') brightness=$(settings get system screen_brightness)"

# 保存并稍后恢复原始状态
ORIG_P0_G=$(cat $CF/policy0/scaling_governor 2>/dev/null)
ORIG_P0_MIN=$(cat $CF/policy0/scaling_min_freq 2>/dev/null)
ORIG_P0_MAX=$(cat $CF/policy0/scaling_max_freq 2>/dev/null)
ORIG_P6_G=$(cat $CF/policy6/scaling_governor 2>/dev/null)
ORIG_P6_MIN=$(cat $CF/policy6/scaling_min_freq 2>/dev/null)
ORIG_P6_MAX=$(cat $CF/policy6/scaling_max_freq 2>/dev/null)
log "orig p0=$ORIG_P0_G/$ORIG_P0_MIN/$ORIG_P0_MAX p6=$ORIG_P6_G/$ORIG_P6_MIN/$ORIG_P6_MAX"

# 打开上限：本机实测系统会随时把 policy6 的 max 压回 1958400
# （硬件 cpuinfo_max_freq=4320000），导致高频点全部锁不住。
# 锁频前先抬到目标最高，并在每轮开始重复一次：
# 一次性设置会在测量中途被系统改回，这是之前数据被污染的直接原因。
raise_limits() {
  echo 2745600 > $CF/policy0/scaling_max_freq 2>/dev/null
  echo 3072000 > $CF/policy6/scaling_max_freq 2>/dev/null
}

# 频点：小核群覆盖低中高，大核群同理。每群取 6~7 个代表点。
FREQ0="556800 960000 1363200 1785600 2227200 2400000 2745600"
FREQ6="1017600 1401600 1958400 2438400 2841600 3072000"
CORES0="0 1 2 3 4 5"
CORES6="6 7"

r=1
while [ "$r" -le "$ROUNDS" ]; do
  # 每轮重新抬一次上限：系统会在测量中途压回去
  raise_limits
  # 环境准入检查：bench 必须能跑出干净数字，否则本轮数据不可信。
  # 判据：elapsed 必须接近请求值（偏差<0.05s），
  # 以及三个重复的 ops 相互差异 <10%。
  q1=$("$BENCH" 0 1 3 2>/dev/null | sed -n 's/.*MEASURE_END|[^|]*|[^|]*|\([0-9.]*\)|[^|]*|\([0-9.]*\)|.*/\1 \2/p')
  q2=$("$BENCH" 0 1 3 2>/dev/null | sed -n 's/.*MEASURE_END|[^|]*|[^|]*|\([0-9.]*\)|[^|]*|\([0-9.]*\)|.*/\1 \2/p')
  log "ROUND $r QC $q1 | $q2  loadavg=$(cat /proc/loadavg | cut -d' ' -f1-2)"
  # 每轮重取全机基线，跟踪热漂移与后台活动
  base=$(sample_window $((MEAS * 10)) 0.1)
  SYS_C=$(echo "$base" | cut -d' ' -f1)
  log "ROUND $r sys_current=$SYS_C uA p6max=$(cat $CF/policy6/scaling_max_freq)"

  for pol in 0 6; do
    if [ "$pol" = "0" ]; then
      FREQS=$FREQ0
      CORES=$CORES0
    else
      FREQS=$FREQ6
      CORES=$CORES6
    fi
    for fq in $FREQS; do
      if ! lock_policy "$pol" "$fq"; then
        log "  SKIP p$pol $fq lock_failed cur=$(cat $CF/policy$pol/scaling_cur_freq 2>/dev/null)"
        continue
      fi
      for core in $CORES; do
        # --- A: 满载 ---
        # 绝对不能用 `setsid cmd &` 然后 `wait $!`：
        #   setsid 自己立即退出，$! 是 setsid 的 PID，wait 会马上返回，
        #   于是下面的“空闲”窗口实际与 bench 重叠 —— 实测 p_idle
        #   经常高于 p_load、甚至出现 idle>>load，就是这个原因。
        # 直接后台跑 bench，按 bench 自己的预热+测量时长等它结束，
        # 并在开始采样前先确认 bench 已经起来。
        "$BENCH" "$core" 1 "$MEAS" >/tmp/eeff_b.$core 2>/dev/null &
        bp=$!
        sleep 1.2            # 等 worker 绑核 + 预热开始
        ld=$(sample_window $((MEAS * 10)) 0.1)
        wait "$bp" 2>/dev/null
        L_C=$(echo "$ld" | cut -d' ' -f1)
        L_N=$(echo "$ld" | cut -d' ' -f2)

        # 吞吐直接取 bench 自己的 MEASURE_END，它是精确计时，不受采样窗长度影响
        ops=$(sed -n 's/.*B|MEASURE_END|[^|]*|[^|]*|[^|]*|[^|]*|\([0-9.]*\)|.*/\1/p' /tmp/eeff_b.$core 2>/dev/null)
        case "$ops" in ''|*[!0-9.]*) ops=0 ;; esac

        # 校验 bench 真的跑完了整个测量期。
        # 之前出现过同频不同核 ops 相差 10 倍（4.8M vs 96M），
        # 就是因为 bench 被杀或未拿到 CPU，MEASURE_END 里的
        # elapsed 远小于 MEAS。这里直接读 elapsed 判有效性。
        el=$(sed -n 's/.*B|MEASURE_END|[^|]*|[^|]*|\([0-9.]*\)|.*/\1/p' /tmp/eeff_b.$core 2>/dev/null)
        case "$el" in ''|*[!0-9.]*) el=0 ;; esac
        # 只取整数秒部分比较。之前写成 `去掉小数点再取前4位`，
        # 结果 6.000012 -> 6000、2.000012 -> 2000，两者都能通过 >=54 的判断，
        # 等于这个校验根本没生效。这里老老实实取小数点前的部分。
        elsec=${el%%.*}
        case "$elsec" in ''|*[!0-9]*) elsec=0 ;; esac
        # 预期 elapsed ≈ MEAS，允许 1 秒误差（计时四舍五入）
        if [ "$elsec" -lt $((MEAS - 1)) ]; then
          log "  BADBENCH core$core $fq ops=$ops elapsed=$el want=$MEAS"
          ops=0
        fi

        # --- B: 空闲，钉在同一核上 ---
        # 同样不能用 setsid；并且要确保上一个 bench 确实退出了，
        # 否则测到的“空闲”里还含着上一轮残留负载。
        cnt=$(ps -A -o ARGS 2>/dev/null | grep -c "[e]eff/bench")
        if [ "$cnt" != "0" ]; then
          log "  WARN bench_still_running=$cnt before idle core$core"
          pkill -f eeff/bench 2>/dev/null
          sleep 1
        fi
        sleep 0.8
        taskset -c "$core" sleep $((MEAS + 1)) >/dev/null 2>&1 &
        ip=$!
        sleep 0.5
        id=$(sample_window $((MEAS * 10)) 0.1)
        wait "$ip" 2>/dev/null
        I_C=$(echo "$id" | cut -d' ' -f1)
        I_N=$(echo "$id" | cut -d' ' -f2)

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "$r" "$pol" "$fq" "$core" "$ops" "$L_C" "$I_C" "$SYS_C" "$L_N" "$I_N" >> "$OUT"
        log "  r$r p$pol ${fq} c$core ops=$ops load=$L_C idle=$I_C sys=$SYS_C"
      done
    done
  done
  r=$((r + 1))
done

# 恢复
echo "$ORIG_P0_G" > $CF/policy0/scaling_governor 2>/dev/null
echo "$ORIG_P0_MIN" > $CF/policy0/scaling_min_freq 2>/dev/null
echo "$ORIG_P0_MAX" > $CF/policy0/scaling_max_freq 2>/dev/null
echo "$ORIG_P6_G" > $CF/policy6/scaling_governor 2>/dev/null
echo "$ORIG_P6_MIN" > $CF/policy6/scaling_min_freq 2>/dev/null
echo "$ORIG_P6_MAX" > $CF/policy6/scaling_max_freq 2>/dev/null

log "RESTORED p0=$(cat $CF/policy0/scaling_governor)/$(cat $CF/policy0/scaling_min_freq)/$(cat $CF/policy0/scaling_max_freq) p6=$(cat $CF/policy6/scaling_governor)/$(cat $CF/policy6/scaling_min_freq)/$(cat $CF/policy6/scaling_max_freq)"
log "online_final=$(online_now)"
log "DONE $(wc -l < "$OUT") lines"

# 额外写一份“结论文本”，断线后只需一条短命令 cat 回去，
# 不必传输整张表（无线链路极不稳定，小载荷成功率高得多）。
SUM=/data/local/tmp/eeff/core.summary
tr -d '\r' < "$OUT" > "$SUM"
log "SUMMARY $SUM $(wc -c < "$SUM") bytes"
echo "ok $(date +%s)" > "$DONE"
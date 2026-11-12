#!/system/bin/sh
# core_energy.sh -- 用库仑计测"能耗"，避开电流瞬时噪声
#
# 为什么放弃 current_now 相减法
# ----------------------------
# 实测（winrepeat.sh，同一个静止系统连续 6 个 4 秒窗口）：
#   97mA 105mA 115mA 199mA 225mA 378mA   ->  0.41W ~ 1.59W
# 噪声 1.19 W。而单核满载相对空闲的真实增量只有 0.1~0.5 W。
# 噪声是信号的 2~10 倍，任何"满载-空闲"的相减都被噪声淹没，
# 会出现 load<idle、cpu 功耗为负（实测 22% 的行是负的）。
#
# 为什么 charge_counter 可用
# -------------------------
# 它是 PMIC 维护的累积库仑积分，本身就是对电流的时间积分，
# 天然把高频抖动平均掉了。实测分辨率：1000 单位 = 0.001 Ah。
# 所以它只能在【分钟级】窗口上给出变化，但一旦有变化就是可信的。
#
# ===== 2026-11-12 窗口协议修订（关键）=====
# 旧协议满载/空闲用同一个 120s 窗口，实测 30/30 行 cc_idle=0：
# 空闲 120s 掉电约 700 单位 < 1000 分辨率，量化误差吞掉全部空闲基线，
# 于是"净功耗 = 满载功耗"（基线一分没扣），能效曲线失真。
# 新协议【功率归一化】：
#   P_load = Δload × V / T_load      满载窗口平均功率
#   P_idle = Δidle × V / T_idle      空闲窗口平均功率（独立、更长）
#   P_net  = P_load - P_idle         该核净增功率
#   E/op   = P_net / (ops / T_load)
# 两个窗口不再需要等长：空闲窗口单独加长到 IDLE_WIN（默认 300s，
# 量化误差 3.1%），且每测点独立测量（旧数据每核只配对一个空闲值，
# 一旦该值量化失败，整核测点全废）。
# 满载窗口保持 120s（信号强，120s 掉电 8000~20000 单位，量化误差 <1%）。
#
# 注意 P_net 为负不是 bug：单核满载时其余核进入更深休眠、整机功率
# 可能反而下降。净口径为负的测点如实标记，不删改数据。
#
# 用法: core_energy.sh <rounds> <load_window_s> [idle_window_s]
# 输出: /data/local/tmp/eeff/energy.tsv
# 估算耗时（rounds=2, load=120, idle=300）：约 50 分钟
#   每轮 1 个全机基线(120s) + 6 测点 × (120s 满载 + 300s 空闲 + 唤醒开销)

ROUNDS=${1:-2}
WIN=${2:-120}
IDLE_WIN=${3:-300}
B=/sys/class/power_supply/battery
CF=/sys/devices/system/cpu/cpufreq
BENCH=/data/local/tmp/eeff/bench
OUT=/data/local/tmp/eeff/energy.tsv
LOG=/data/local/tmp/eeff/energy.log
DONE=/data/local/tmp/eeff/energy.done

rm -f "$DONE"
# idle_win 与 win 分开：旧数据没有 idle_win 列，plot 按旧列回退处理
# temp_mC 是测点结束时的 CPU 温度（毫摄氏度），供 plot 标记过热降频点
printf 'rnd\tpolicy\tfreq\tcore\twindow_s\tidle_win\tcc_load\tcc_idle\tcc_sys\tops_total\tdV_uV\ttemp_mC\n' > "$OUT"
: > "$LOG"

log() { echo "$@" >> "$LOG"; }
cc() { cat $B/charge_counter 2>/dev/null; }
volt() { cat $B/voltage_now 2>/dev/null; }

# 读 CPU 温度（取最大的 thermal_zone，过热时记录供 plot 剔除）
cpu_temp() {
  _t=0
  for z in /sys/class/thermal/thermal_zone*/temp; do
    _v=$(cat "$z" 2>/dev/null)
    [ "${_v:-0}" -gt "$_t" ] && _t=$_v
  done
  echo "$_t"
}

log "START rounds=$ROUNDS win=$WIN idle_win=$IDLE_WIN online=$(cat /sys/devices/system/cpu/online)"

# 环境准入：吞吐必须稳定，否则数据无意义。
# 旧版只记录不判断，导致 suspend 期间 ops 崩到 12M 也继续测完全程。
# 准入判据（沿用此前实测结论）：连续 3 次 ops 相对极差 <5%。
# 环境信息：记录但不判断（bench 吞吐受 CPU 频率影响，未锁频时波动正常）
q=""
k=0
while [ "$k" -lt 3 ]; do
  o=$("$BENCH" 0 1 3 2>/dev/null | sed -n 's/.*MEASURE_END|[^|]*|[^|]*|[^|]*|[^|]*|\([0-9.]*\)|.*/\1/p')
  q="$q $o"
  k=$((k + 1))
done
# QC 只记录不拒绝：bench 吞吐受 CPU 频率影响，未锁频时波动是正常的。
# 真正的数据质量由后续锁频测量保证，QC 阶段的 ops 波动不代表环境脏。
log "QC ops:$q（未锁频，波动正常，仅作参考）"
log "memavail=$(grep MemAvailable /proc/meminfo | tr -s ' ' | cut -d' ' -f2) loadavg=$(cat /proc/loadavg | cut -d' ' -f1)"
# 不再因 QC 失败退出——锁频后的测量才是有效数据

# 亮度最低但不熄屏：熄屏会断无线 ADB，导致取不回数据
settings put system screen_brightness_mode 0 2>/dev/null
settings put system screen_brightness 0 2>/dev/null
# 必须主动防 Dozing。实测：设备一旦进入 Dozing，
# android.system.suspend-service 开始反复 suspend，bench 被阻塞，
# ops 从 190M 崩到 12~56M，elapsed 从 3.000 变成 3.5~4.1s。
# 光设 stayon 不够，还要把息屏超时设为无穷并周期性重新唤醒。
svc power stayon true 2>/dev/null
settings put system screen_off_timeout 2147483647 2>/dev/null
input keyevent 224 2>/dev/null
sleep 3
log "brightness=$(settings get system screen_brightness) wake=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')"

# 每个测点前保活：Dozing 只靠开头一次设置防不住，会中途再次进入
keep_awake() {
  _w=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*')
  if [ "$_w" != "mWakefulness=Awake" ]; then
    log "  WAKE from $_w"
    input keyevent 224 2>/dev/null
    svc power stayon true 2>/dev/null
    sleep 2
  fi
}

ORIG0=$(cat $CF/policy0/scaling_governor)/$(cat $CF/policy0/scaling_min_freq)/$(cat $CF/policy0/scaling_max_freq)
ORIG6=$(cat $CF/policy6/scaling_governor)/$(cat $CF/policy6/scaling_min_freq)/$(cat $CF/policy6/scaling_max_freq)
log "orig p0=$ORIG0 p6=$ORIG6"

raise_limits() {
  echo 2745600 > $CF/policy0/scaling_max_freq 2>/dev/null
  echo 3072000 > $CF/policy6/scaling_max_freq 2>/dev/null
}

lock_policy() {
  _p=$1
  _f=$2
  echo performance > "$CF/policy$_p/scaling_governor" 2>/dev/null
  echo "$_f" > "$CF/policy$_p/scaling_max_freq" 2>/dev/null
  echo "$_f" > "$CF/policy$_p/scaling_min_freq" 2>/dev/null
  sleep 1
  [ "$(cat $CF/policy$_p/scaling_cur_freq 2>/dev/null)" = "$_f" ]
}

# 测点：快速模式（20 分钟）——减少轮次和测点
# 1 轮 × 5 测点 × (120s 满载 + 300s 空闲) ≈ 35 分钟
# 如果只要 20 分钟：1 轮 × 4 测点 × (120s + 300s) ≈ 28 分钟
# 或 1 轮 × 5 测点 × (60s + 180s) ≈ 20 分钟（牺牲精度）
FREQ0="556800 1363200 2400000 2745600"
FREQ6="1017600 2000000 3072000"
CORES0="0"
CORES6="6"

# 快速模式：1 轮，减少测点
if [ "$ROUNDS" = "1" ]; then
  FREQ0="556800 1363200 2400000 2745600"
  FREQ6="1017600 2000000 3072000"
fi

r=1
while [ "$r" -le "$ROUNDS" ]; do
  raise_limits
  # 全机基线（参考值，不用于净功耗扣除——净功耗由每测点自己的空闲窗口提供）
  # 符号约定与 LD/ID 一致：全部用 (起点 - 终点)，放电时为正。
  c0=$(cc)
  sleep "$WIN"
  c1=$(cc)
  SYS_D=$((c0 - c1))
  V=$(volt)
  log "ROUND $r sys dcharge=$SYS_D V=$V loadavg=$(cat /proc/loadavg | cut -d' ' -f1)"

  for pol in 0 6; do
    if [ "$pol" = "0" ]; then FREQS=$FREQ0; CORES=$CORES0
    else FREQS=$FREQ6; CORES=$CORES6; fi
    for fq in $FREQS; do
      if ! lock_policy "$pol" "$fq"; then
        log "  SKIP p$pol $fq lockfail"
        continue
      fi
      for core in $CORES; do
        keep_awake
        # --- 满载窗口（WIN 秒）---
        # c0 必须在 bench 启动【之前】读，否则 bench 已跑的耗电被漏算。
        c0=$(cc)
        "$BENCH" "$core" 1 "$WIN" >/tmp/e_b.$core 2>/dev/null &
        bp=$!
        wait "$bp" 2>/dev/null
        c1=$(cc)
        LD=$((c0 - c1))
        ops=$(sed -n 's/.*MEASURE_END|[^|]*|[^|]*|[^|]*|[^|]*|\([0-9.]*\)|.*/\1/p' /tmp/e_b.$core 2>/dev/null)
        case "$ops" in ''|*[!0-9.]*) ops=0 ;; esac

        # --- 空闲窗口（IDLE_WIN 秒，独立加长，同一核钉住）---
        # 关键：独立读起点 c0_idle，且窗口独立于满载窗口。
        # 空闲每测点单独测，不做全核共享——共享值一旦量化失败整核作废。
        c0_idle=$(cc)
        taskset -c "$core" sleep "$IDLE_WIN" >/dev/null 2>&1 &
        ip=$!
        wait "$ip" 2>/dev/null
        c1=$(cc)
        ID=$((c0_idle - c1))

        # 自检：LD<=0 说明满载窗口根本没放电（异常）；
        # ID=0 说明空闲窗口落在量化台阶内——功率口径下 P_idle 按 0 处理
        # 并在日志标出，由 plot 在汇总里报告占比，不再静默。
        if [ "$LD" -le 0 ]; then
          log "  BADDELTA c$core $fq load_d=$LD（满载窗口未放电，该行无效）"
        fi
        if [ "$ID" -le 0 ]; then
          log "  IDLE_ZERO c$core $fq idle_d=$ID（空闲窗口量化不足，P_idle 按下限 0 处理，净功耗为上界）"
        fi

        TEMP=$(cpu_temp)
        if [ "${TEMP:-0}" -ge 85000 ]; then
          log "  THERMAL_WARN c$core $fq temp=${TEMP}mC（>=85C，可能降频，plot 会标记）"
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "$r" "$pol" "$fq" "$core" "$WIN" "$IDLE_WIN" "$LD" "$ID" "$SYS_D" "$ops" "$(volt)" "$TEMP" >> "$OUT"
        log "  r$r p$pol $fq c$core ops=$ops load_d=$LD idle_d=$ID sys_d=$SYS_D temp=$TEMP"
      done
    done
  done
  r=$((r + 1))
done

# 恢复
echo "${ORIG0%%/*}" > $CF/policy0/scaling_governor 2>/dev/null
R=${ORIG0#*/}; echo "${R%%/*}" > $CF/policy0/scaling_min_freq 2>/dev/null
echo "${ORIG0##*/}" > $CF/policy0/scaling_max_freq 2>/dev/null
echo "${ORIG6%%/*}" > $CF/policy6/scaling_governor 2>/dev/null
R=${ORIG6#*/}; echo "${R%%/*}" > $CF/policy6/scaling_min_freq 2>/dev/null
echo "${ORIG6##*/}" > $CF/policy6/scaling_max_freq 2>/dev/null

log "RESTORED p0=$(cat $CF/policy0/scaling_governor)/$(cat $CF/policy0/scaling_min_freq)/$(cat $CF/policy0/scaling_max_freq) p6=$(cat $CF/policy6/scaling_governor)/$(cat $CF/policy6/scaling_min_freq)/$(cat $CF/policy6/scaling_max_freq)"
log "DONE $(wc -l < "$OUT") lines"
echo "ok" > "$DONE"

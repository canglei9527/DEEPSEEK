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
# 天然把高频抖动平均掉了。实测分辨率：每分钟 1000 单位 = 0.001 Ah。
# 所以它只能在【分钟级】窗口上给出变化，但一旦有变化就是可信的。
#
# 测量量改为【能耗】而不是【功率】：
#   E = Δcharge(库仑) × V ， 每次测固定时长 T 的固定工作量
#   能耗 per op = E / (ops × T)
# 这样不需要知道瞬时功率，只需要一段时间内总共消耗了多少电荷。
#
# 协议（每个 core x freq）
#   A. 满载 T 秒：记录 Δcharge_load 与 bench 总 ops
#   B. 空闲 T 秒：记录 Δcharge_idle
#   Net = Δcharge_load - Δcharge_idle  （电量差，正数才是真实增量）
# 重复 ROUNDS 轮。
#
# 用法: core_energy.sh <rounds> <window_s>
# 输出: /data/local/tmp/eeff/energy.tsv

ROUNDS=${1:-3}
WIN=${2:-120}
B=/sys/class/power_supply/battery
CF=/sys/devices/system/cpu/cpufreq
BENCH=/data/local/tmp/eeff/bench
OUT=/data/local/tmp/eeff/energy.tsv
LOG=/data/local/tmp/eeff/energy.log
DONE=/data/local/tmp/eeff/energy.done

rm -f "$DONE"
printf 'rnd\tpolicy\tfreq\tcore\twindow_s\tcc_load\tcc_idle\tcc_sys\tops_total\tdV_uV\n' > "$OUT"
: > "$LOG"

log() { echo "$@" >> "$LOG"; }
cc() { cat $B/charge_counter 2>/dev/null; }
volt() { cat $B/voltage_now 2>/dev/null; }

log "START rounds=$ROUNDS win=$WIN online=$(cat /sys/devices/system/cpu/online)"

# 环境准入：吞吐必须稳定，否则数据无意义
q=""
k=0
while [ "$k" -lt 3 ]; do
  o=$("$BENCH" 0 1 3 2>/dev/null | sed -n 's/.*MEASURE_END|[^|]*|[^|]*|[^|]*|[^|]*|\([0-9.]*\)|.*/\1/p')
  q="$q $o"
  k=$((k + 1))
done
log "QC ops:$q"
log "memavail=$(grep MemAvailable /proc/meminfo | tr -s ' ' | cut -d' ' -f2) loadavg=$(cat /proc/loadavg | cut -d' ' -f1)"

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

# 频点按文件大小权衡：分钟级窗口 + 多轮成本高，取每群代表点
FREQ0="556800 1363200 2400000 2745600"
FREQ6="1017600"
CORES0="0"
CORES6="6"

r=1
while [ "$r" -le "$ROUNDS" ]; do
  raise_limits
  # 全机基线
  # 符号约定必须与下面的 LD/ID 一致：全部用 (起点 - 终点)，放电时为正。
  # 早先这里写成 c1 - c0，得到 sys_d=-12000，而 LD/ID 是正数，
  # 导致 plot 里 above = dl - ds = 5000-(-12000) = 17000，
  # 正确应为 5000-12000 = -7000，整整差 24000 单位。
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
        # --- 满载窗口 ---
        # 注意取值时机：c0 必须在 bench 启动【之前】读，
        # 否则 bench 已跑了 2 秒，这段耗电被漏算，
        # 会让满载电量偏小而错误地显得"省电"。
        c0=$(cc)
        "$BENCH" "$core" 1 "$WIN" >/tmp/e_b.$core 2>/dev/null &
        bp=$!
        wait "$bp" 2>/dev/null
        c1=$(cc)
        LD=$((c0 - c1))
        ops=$(sed -n 's/.*MEASURE_END|[^|]*|[^|]*|[^|]*|[^|]*|\([0-9.]*\)|.*/\1/p' /tmp/e_b.$core 2>/dev/null)
        case "$ops" in ''|*[!0-9.]*) ops=0 ;; esac

        # --- 空闲窗口（同一核钉住）---
        # 关键：必须在这里重新读一次起点 c0_idle。
        # 早先写成直接复用满载的 c0，结果 ID 算的是"满载+空闲"两段的
        # 总耗电（如 load_d=5000 idle_d=5000），两段永远不会分开。
        c0_idle=$(cc)
        taskset -c "$core" sleep "$WIN" >/dev/null 2>&1 &
        ip=$!
        wait "$ip" 2>/dev/null
        c1=$(cc)
        ID=$((c0_idle - c1))

        # 自检：两个窗口都跑了却得到 0 差，说明电量窗口没覆盖住负载
        # （例如库仑计被 PMIC 平滑、或窗口太短落在量化台阶内），
        # 这种行不能静默写出，必须标记出来。
        if [ "$LD" -le 0 ] || [ "$ID" -le 0 ]; then
          log "  BADDELTA c$core $fq load_d=$LD idle_d=$ID"
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "$r" "$pol" "$fq" "$core" "$WIN" "$LD" "$ID" "$SYS_D" "$ops" "$(volt)" >> "$OUT"
        log "  r$r p$pol $fq c$core ops=$ops load_d=$LD idle_d=$ID sys_d=$SYS_D"
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
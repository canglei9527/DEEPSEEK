#!/system/bin/sh
# drain-audit.sh -- 功耗诊断总控（一次跑完，输出可当基线参考）
#
# 这是本套件的主入口。设计目标：
#   1. 一次运行产出一份「结构化、可对比」的功耗报告
#   2. 所有数据都带单位、带采集窗口、带判断结论
#   3. 可以在任何时刻重跑，两次输出可直接 diff
#
# 用法:
#   su -c "sh /data/local/tmp/drain-audit.sh"            # 快速版（约 90 秒）
#   su -c "sh /data/local/tmp/drain-audit.sh full"       # 完整版（约 5 分钟）
#
# 输出:
#   /data/local/tmp/plc/audit-<时间>.txt   人读报告
#   /data/local/tmp/plc/audit-<时间>.tsv   机器可读（每行 key<TAB>value）

MODE=${1:-fast}
D=/data/local/tmp/plc
mkdir -p $D
TS=$(date +%Y%m%d-%H%M%S)
RPT=$D/audit-$TS.txt
TSV=$D/audit-$TS.tsv

if [ "$MODE" = "full" ]; then
  WIN=180
else
  WIN=60
fi

kv() {
  # key<TAB>value<TAB>note
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> $TSV
  printf '  %-32s %-14s %s\n' "$1" "$2" "$3" >> $RPT
}

sec() { printf '\n===== %s =====\n' "$1" >> $RPT; }

: > $RPT
: > $TSV

{
  echo "功耗诊断报告  $TS"
  echo "模式=$MODE  采样窗口=${WIN}s"
  echo "设备: $(getprop ro.product.model) / $(getprop ro.product.device)"
  echo "系统: Android $(getprop ro.build.version.release) (sdk $(getprop ro.build.version.sdk))"
  echo "内核: $(uname -r)"
} >> $RPT

# ---------------- 1. 前提条件 ----------------
sec "1. 前提条件（不满足则后续数据无意义）"

CHG=$(cat /sys/class/power_supply/battery/status 2>/dev/null)
kv "battery_status" "$CHG" "必须 Discharging，否则不挂起"
SCREEN=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*' | cut -d= -f2)
kv "wakefulness" "$SCREEN" "必须非 Awake 才是待机"
BL=$(cat /sys/class/backlight/panel0-backlight/brightness 2>/dev/null)
kv "backlight" "${BL:-n/a}" "0=灭屏"
kv "mem_sleep" "$(cat /sys/power/mem_sleep 2>/dev/null | tr -d '\n')" "[]内为当前模式"
kv "swappiness" "$(cat /proc/sys/vm/swappiness 2>/dev/null)" "100=最激进"
kv "uptime_s" "$(cut -d' ' -f1 /proc/uptime)" "开机时长，越短计数器越干净"

# ---------------- 2. 采样窗口 ----------------
sec "2. 采样窗口（${WIN}s）"

# 2.1 电量与电流
C0=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
V0=$(cat /sys/class/power_supply/battery/voltage_now 2>/dev/null)
I0=$(cat /sys/class/power_supply/battery/current_now 2>/dev/null)

# 2.2 挂起计数
S0=$(cat /sys/power/suspend_stats/success 2>/dev/null || echo 0)
F0=$(cat /sys/power/suspend_stats/fail 2>/dev/null || echo 0)

# 2.3 唤醒源快照
W=/sys/kernel/debug/wakeup_sources
[ -r "$W" ] || mount -t debugfs none /sys/kernel/debug 2>/dev/null
if [ -r "$W" ]; then
  awk 'NR>1 && NF>=8 {print $1"|"$2"|"$3}' $W | sort > $D/_wa0
fi

# 2.4 per-UID CPU 快照
sed 's/: / /' /proc/uid_cputime/show_uid_stat 2>/dev/null | awk '{print $1, $2+$3}' | sort -k1,1n > $D/_ca0

# 2.5 网络与 swap
RX0=$(cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null || echo 0)
TX0=$(cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null || echo 0)
PS0=$(awk '/^pswpin /{print $2}' /proc/vmstat 2>/dev/null || echo 0)
PO0=$(awk '/^pswpout /{print $2}' /proc/vmstat 2>/dev/null || echo 0)
MF0=$(awk '/^pgmajfault /{print $2}' /proc/vmstat 2>/dev/null || echo 0)

# 2.6 中断快照
awk '/^ *[0-9]+:/{gsub(":","",$1); s+=$2} END{print s}' /proc/interrupts 2>/dev/null > $D/_irq0

# 2.7 dmesg 挂起计数
E0=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
A0=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')

T0=$(date +%s)
sleep $WIN
T1=$(date +%s)
DT=$((T1 - T0)); [ "$DT" -lt 1 ] && DT=$WIN

C1=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
V1=$(cat /sys/class/power_supply/battery/voltage_now 2>/dev/null)
I1=$(cat /sys/class/power_supply/battery/current_now 2>/dev/null)
S1=$(cat /sys/power/suspend_stats/success 2>/dev/null || echo 0)
F1=$(cat /sys/power/suspend_stats/fail 2>/dev/null || echo 0)

if [ -r "$W" ]; then
  awk 'NR>1 && NF>=8 {print $1"|"$2"|"$3}' $W | sort > $D/_wa1
fi
sed 's/: / /' /proc/uid_cputime/show_uid_stat 2>/dev/null | awk '{print $1, $2+$3}' | sort -k1,1n > $D/_ca1

RX1=$(cat /sys/class/net/wlan0/statistics/rx_packets 2>/dev/null || echo 0)
TX1=$(cat /sys/class/net/wlan0/statistics/tx_packets 2>/dev/null || echo 0)
PS1=$(awk '/^pswpin /{print $2}' /proc/vmstat 2>/dev/null || echo 0)
PO1=$(awk '/^pswpout /{print $2}' /proc/vmstat 2>/dev/null || echo 0)
MF1=$(awk '/^pgmajfault /{print $2}' /proc/vmstat 2>/dev/null || echo 0)
awk '/^ *[0-9]+:/{gsub(":","",$1); s+=$2} END{print s}' /proc/interrupts 2>/dev/null > $D/_irq1
IRQ1=$(cat $D/_irq1); IRQ0=$(cat $D/_irq0)
E1=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
A1=$(dmesg 2>/dev/null | grep -c 'Abort: Pending Wakeup Sources')

# ---------------- 3. 核心指标 ----------------
sec "3. 核心指标（这是判断"耗电快不快"的唯一依据）"

MAH=$(awk -v a=$C0 -v b=$C1 'BEGIN{printf "%.1f", (a-b)/1000}')
MA=$(awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{printf "%.1f", (a-b)/1000*3600/t}')
PCTH=$(awk -v a=$C0 -v b=$C1 -v t=$DT -v cap=6100000 'BEGIN{printf "%.2f", (a-b)/cap*3600/t*100}')

kv "window_s" "$DT" ""
kv "discharge_mAh" "$MAH" "电量计差值"
kv "current_mA" "$MA" "平均电流"
kv "rate_pct_per_h" "$PCTH" "按 6100mAh 设计容量折算"
kv "instant_current_uA" "$I1" "瞬时（不可靠，仅参考）"
kv "voltage_uV" "$V1" ""

sec "4. 挂起健康度（最关键，决定一切）"
SD=$((S1 - S0)); FD=$((F1 - F0))
TOT=$((SD + FD))
if [ "$TOT" -gt 0 ]; then
  FAILR=$((FD * 100 / TOT))
else
  FAILR=0
fi
kv "suspend_success_delta" "$SD" ""
kv "suspend_fail_delta" "$FD" ""
kv "suspend_fail_pct" "${FAILR}%" ">40% 说明设备基本没睡下去"
kv "suspend_entry_dmesg" "$((E1 - E0))" "dmesg 里 entry 次数"
kv "suspend_abort_dmesg" "$((A1 - A0))" "被打断次数"
kv "last_failed_dev" "$(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null)" ""
kv "last_failed_errno" "$(cat /sys/power/suspend_stats/last_failed_errno 2>/dev/null)" "-16=EBUSY -11=EAGAIN"

sec "5. 唤醒源速率（谁把设备拽出来）"
if [ -f $D/_wa0 ] && [ -f $D/_wa1 ]; then
  awk -F'|' -v dt=$DT '
    FNR==NR { a[$1]=$2; e[$1]=$3; next }
    {
      de = $3 - e[$1]
      if (de > 0) printf "%s\t%s\t%d\n", ($3-e[$1])/dt, $1, de
    }' $D/_wa0 $D/_wa1 | sort -nr | head -n 15 | \
  while IFS="$(printf '\t')" read rate name de; do
    printf '  %-34s %8s 次/秒   (窗口内 %s 次)\n' "$name" "$rate" "$de" >> $RPT
    printf 'wakeup_%s\t%s\t次/秒\n' "$name" "$rate" >> $TSV
  done
else
  echo "  wakeup_sources 不可读" >> $RPT
fi

sec "6. per-UID CPU（谁在算）"
if [ -f $D/_ca0 ] && [ -f $D/_ca1 ]; then
  awk -v dt=$DT '
    FNR==NR { p[$1]=$2; next }
    { if ($1 in p) { d=$2-p[$1]; if (d>100000) printf "%d\t%d\n", d, $1 } }
  ' $D/_ca0 $D/_ca1 | sort -nr | head -n 12 | \
  while IFS="$(printf '\t')" read d uid; do
    pkg=""
    [ -r /data/system/packages.list ] && pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list)
    [ -z "$pkg" ] && case $uid in
      0) pkg="root/内核";; 1000) pkg="system_server";; 1001) pkg="radio";;
      1047) pkg="wifi";; *) pkg="uid$uid";;
    esac
    ms=$((d / 1000))
    pct=$((d * 100 / (dt * 1000000)))
    printf '  %-40s %7d ms  %3d%%\n' "$pkg" "$ms" "$pct" >> $RPT
    printf 'cpu_%s\t%s\tms\n' "$pkg" "$ms" >> $TSV
  done
fi

sec "7. I/O 与内存压力"
kv "wlan_rx_pkt_per_s" "$(( (RX1 - RX0) / DT ))" "Wi-Fi 收包"
kv "wlan_tx_pkt_per_s" "$(( (TX1 - TX0) / DT ))" "Wi-Fi 发包"
kv "pswpin_per_s" "$(( (PS1 - PS0) / DT ))" "换入页/秒，高=thrashing"
kv "pswpout_per_s" "$(( (PO1 - PO0) / DT ))" "换出页/秒"
kv "pgmajfault_per_s" "$(( (MF1 - MF0) / DT ))" "重大缺页/秒"
kv "irq_per_s" "$(( (IRQ1 - IRQ0) / DT ))" "中断/秒"
kv "swap_used_mb" "$(awk 'NR==2{print int($4/1024)}' /proc/swaps 2>/dev/null)" ""
kv "mem_available_mb" "$(grep MemAvailable /proc/meminfo | awk '{print int($2/1024)}')" ""
kv "psi_io_some_avg10" "$(awk '/^some/{print $2}' /proc/pressure/io 2>/dev/null | cut -d= -f2)" "真实 I/O 压力"
kv "psi_mem_some_avg10" "$(awk '/^some/{print $2}' /proc/pressure/memory 2>/dev/null | cut -d= -f2)" ""

sec "8. 进程/线程规模"
set -- /proc/[0-9]*
P=$#
T=0
for d in /proc/[0-9]*; do
  set -- "$d"/task/*
  T=$((T + $#))
done
kv "processes" "$P" ""
kv "threads" "$T" ""
kv "loadavg_1m" "$(cut -d' ' -f1 /proc/loadavg)" "含 D 态，不等于 CPU 忙"

sec "9. 结论自动判读"
{
  if [ "$CHG" != "Discharging" ]; then
    echo "  [!] 当前在充电，本次数据不反映待机功耗"
  fi
  if [ "$SCREEN" = "Awake" ]; then
    echo "  [!] 屏幕亮着，本次数据是使用态而非待机态"
  fi
  if [ "$FAILR" -gt 40 ]; then
    echo "  [核心问题] 挂起失败率 ${FAILR}%，设备大部分时间没睡下去"
    echo "             last_failed_dev=$(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null)"
  elif [ "$FAILR" -gt 15 ]; then
    echo "  [注意] 挂起失败率 ${FAILR}%，有改善空间"
  else
    echo "  [健康] 挂起失败率 ${FAILR}%"
  fi
  MAI=$(awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{printf "%.0f", (a-b)/1000*3600/t}')
  if [ "$MAI" -gt 250 ]; then
    echo "  [核心问题] 电流 ${MAI} mA，远高于应有的 20-60 mA"
  elif [ "$MAI" -gt 120 ]; then
    echo "  [偏高] 电流 ${MAI} mA，目标应 < 80 mA"
  elif [ "$MAI" -gt 60 ]; then
    echo "  [可接受] 电流 ${MAI} mA"
  else
    echo "  [优秀] 电流 ${MAI} mA"
  fi
  PSI=$(awk '/^some/{print $2}' /proc/pressure/io 2>/dev/null | cut -d= -f2 | cut -d. -f1)
  if [ "${PSI:-0}" -lt 1 ]; then
    echo "  [提示] PSI io=${PSI}%，磁盘 I/O 不是问题；若把 iowait 当主因需复核 awk 字段"
  fi
} >> $RPT

rm -f $D/_wa0 $D/_wa1 $D/_ca0 $D/_ca1 $D/_irq0 $D/_irq1

echo "" >> $RPT
echo "报告: $RPT" >> $RPT
echo "数据: $TSV" >> $RPT

cat $RPT
#!/system/bin/sh
# winrepeat.sh -- 量化"窗口均值"的重复性
#
# 为什么必须放在设备端
# --------------------
# 无线 ADB 极不稳定：每次长命令（>20s）都有概率把连接打成 offline。
# 而测量需要几十个长时间窗，若每个窗都过一次 ADB，中途必断。
# 所以把循环整个搬到设备端，一次调用只回传最终数字。
#
# 目的：判断"扣除基线"是否可行。
#   功率必须靠相减得到（满载 → 空闲），相减要求两次窗口的均值稳定。
#   如果窗口与窗口之间的均值波动 > 核间差异，这个减法就没意义。

N_WIN=${1:-8}
N_SAMPLE=${2:-20}
SLEEP=${3:-0.15}
B=/sys/class/power_supply/battery
T=/data/local/tmp/eeff/winrepeat.log

: > "$T"
i=0
while [ "$i" -lt "$N_WIN" ]; do
  s=0
  n=0
  j=0
  while [ "$j" -lt "$N_SAMPLE" ]; do
    c=$(cat $B/current_now 2>/dev/null)
    v=$(cat $B/voltage_now 2>/dev/null)
    case "$c" in ''|*[!0-9-]*) c=0 ;; esac
    s=$((s + c))
    n=$((n + 1))
    j=$((j + 1))
    sleep "$SLEEP"
  done
  if [ "$n" -gt 0 ]; then
    echo "WIN $i $((s / n)) $n $v" >> "$T"
  fi
  i=$((i + 1))
done
cat "$T"
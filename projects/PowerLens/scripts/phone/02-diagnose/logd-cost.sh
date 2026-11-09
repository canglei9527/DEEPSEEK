#!/system/bin/sh
# logd-cost.sh -- 精确测量 logd 的功耗占比，判断是否值得优化
# 用法: su -c "sh /data/local/tmp/logd-cost.sh [秒数，默认60]"

WIN=${1:-60}

echo "===== logd 开销测量（${WIN}s）====="

P=$(pidof logd)
[ -z "$P" ] && { echo "logd 未运行"; exit 1; }

# --- CPU ---
A=$(awk '{print $14+$15}' /proc/$P/stat)
# --- 全系统 CPU 基线 ---
S0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9}' /proc/stat)
# --- 电流 ---
C0=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
T0=$(date +%s)

sleep $WIN

B=$(awk '{print $14+$15}' /proc/$P/stat)
S1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9}' /proc/stat)
C1=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
T1=$(date +%s)

DT=$((T1 - T0)); [ $DT -lt 1 ] && DT=$WIN
LOGDT=$(( (B-A) * 10 ))          # ms
SYSDT=$(( S1 - S0 ))             # jiffies
SYSMS=$(( SYSDT * 10 ))

awk -v lbl="$WIN" \
    -v logms=$LOGDT -v sysms=$SYSMS \
    -v c0=$C0 -v c1=$C1 -v dt=$DT -v cores=8 'BEGIN{
  printf "  logd CPU:  %d ms / %ds  = %.2f%% 单核\n", logms, lbl, logms/(lbl*10)
  if (sysms > 0) printf "  系统总CPU: %d ms（8 核合计）\n", sysms
  if (sysms > 0) printf "  logd 占系统: %.2f%%\n", logms*100/sysms
  if (c0 > 0 && c1 > 0) {
    ma = (c0-c1)/1000*3600/dt
    printf "  整机电流:  %.1f mA = %.2f W (@3.85V)\n", ma, ma*3.85/1000
    if (sysms > 0) {
      logw = ma * logms / sysms
      printf "  推算 logd 功耗: %.3f W（按 CPU 占比线性分摊，仅粗估）\n", logw
    }
  }
}'

echo ""
echo "===== 判读 ====="
echo "  logd 占单核 X%："
echo "    1-2%   → 约 0.02-0.04W，不是 1W 跳变的原因"
echo "    5-10%  → 约 0.1-0.2W，值得优化"
echo "    >15%   → 约 0.3W+，必须处理"
echo ""
echo "  如果要降到 0.5W（130mA）以下，需要找出占大头的那个"
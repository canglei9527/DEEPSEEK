#!/system/bin/sh
# ab-test.sh -- A/B 对照实验框架（判断某个改动到底有没有用）
#
# ===== 为什么必须用这个 =====
# 本机待机电流在 100-250 mA 之间自然波动（受网络重连、应用活动影响），
# 单次测量的噪声可能大于改动本身的效果。所以：
#   1. 每个条件测多轮，取中位数（不是平均值，避免离群点）
#   2. 交替进行（A B A B）而不是（A A B B），抵消时间漂移
#   3. 每轮都记录当时的屏幕状态/充电状态，不满足就跳过
#
# ===== 用法 =====
#   # 1. 定义两个条件（shell 命令），然后跑
#   su -c "sh /data/local/tmp/ab-test.sh 90 3 'echo 100 > /proc/sys/vm/swappiness' 'echo 60 > /proc/sys/vm/swappiness'"
#
#   # 2. 更常用：测试「停掉某服务」
#   su -c "sh /data/local/tmp/ab-test.sh 90 3 'true' 'stop vendor.adsprpcd; trap_start'"
#
#   # 3. 内置预设
#   su -c "sh /data/local/tmp/ab-test.sh preset swappiness"
#   su -c "sh /data/local/tmp/ab-test.sh preset adsprpcd"
#   su -c "sh /data/local/tmp/ab-test.sh preset deep"
#
# 参数: $1=每轮秒数 $2=轮数 $3=条件A命令 $4=条件B命令

D=/data/local/tmp/plc
mkdir -p $D

measure_once() {
  # 输出: 电流mA<TAB>挂起成功率
  local win=$1
  local c0 s0 f0 t0
  c0=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
  s0=$(cat /sys/power/suspend_stats/success 2>/dev/null || echo 0)
  f0=$(cat /sys/power/suspend_stats/fail 2>/dev/null || echo 0)
  t0=$(date +%s)
  sleep "$win"
  local c1 s1 f1 t1 dt
  c1=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
  s1=$(cat /sys/power/suspend_stats/success 2>/dev/null || echo 0)
  f1=$(cat /sys/power/suspend_stats/fail 2>/dev/null || echo 0)
  t1=$(date +%s)
  dt=$((t1 - t0)); [ "$dt" -lt 1 ] && dt=$win
  awk -v a="$c0" -v b="$c1" -v t="$dt" -v s0="$s0" -v s1="$s1" -v f0="$f0" -v f1="$f1" 'BEGIN{
    ma = (a-b)/1000*3600/t
    sd = s1-s0; fd = f1-f0; tot = sd+fd
    fr = (tot>0) ? fd*100/tot : -1
    printf "%.1f\t%.0f\n", ma, fr
  }'
}

check_precond() {
  local chg scr
  chg=$(cat /sys/class/power_supply/battery/status 2>/dev/null)
  scr=$(dumpsys power 2>/dev/null | grep -o 'mWakefulness=[A-Za-z]*' | cut -d= -f2)
  if [ "$chg" != "Discharging" ]; then
    echo "跳过：正在充电($chg)"
    return 1
  fi
  if [ "$scr" = "Awake" ]; then
    echo "跳过：屏幕亮着，无法测待机"
    return 1
  fi
  return 0
}

median_of() {
  # 从 stdin 读数字，输出中位数
  sort -n | awk '{a[NR]=$1} END{
    if (NR==0) { print "n/a"; exit }
    if (NR%2) print a[(NR+1)/2]
    else printf "%.1f\n", (a[NR/2]+a[NR/2+1])/2
  }'
}

# ---------------- 预设 ----------------
PRESET=$1
if [ "$PRESET" = "preset" ]; then
  P=$2
  case "$P" in
    swappiness)
      WIN=${3:-90}; ROUNDS=${4:-3}
      A_CMD='true'
      B_CMD='echo 60 > /proc/sys/vm/swappiness'
      A_DESC="swappiness=100(原值)"
      B_DESC="swappiness=60"
      RESTORE='echo 100 > /proc/sys/vm/swappiness'
      ;;
    adsprpcd)
      WIN=${3:-90}; ROUNDS=${4:-3}
      A_CMD='true'
      B_CMD='stop vendor.adsprpcd 2>/dev/null'
      A_DESC="adsprpcd 运行"
      B_DESC="adsprpcd 停止"
      RESTORE='start vendor.adsprpcd 2>/dev/null'
      ;;
    deep)
      WIN=${3:-120}; ROUNDS=${4:-2}
      A_CMD='echo s2idle > /sys/power/mem_sleep'
      B_CMD='echo deep > /sys/power/mem_sleep'
      A_DESC="s2idle(默认)"
      B_DESC="deep"
      RESTORE='echo s2idle > /sys/power/mem_sleep'
      ;;
    wifi)
      WIN=${3:-90}; ROUNDS=${4:-3}
      A_CMD='svc wifi enable 2>/dev/null'
      B_CMD='svc wifi disable 2>/dev/null'
      A_DESC="Wi-Fi 开"
      B_DESC="Wi-Fi 关"
      RESTORE='svc wifi enable 2>/dev/null'
      ;;
    data)
      WIN=${3:-90}; ROUNDS=${4:-3}
      A_CMD='svc data enable 2>/dev/null'
      B_CMD='svc data disable 2>/dev/null'
      A_DESC="移动数据开"
      B_DESC="移动数据关"
      RESTORE='svc data enable 2>/dev/null'
      ;;
    *) echo "未知预设: $P"; echo "可用: swappiness adsprpcd deep wifi data"; exit 2 ;;
  esac
else
  WIN=${1:-90}
  ROUNDS=${2:-3}
  A_CMD=$3
  B_CMD=$4
  A_DESC="A: $A_CMD"
  B_DESC="B: $B_CMD"
  RESTORE=""
fi

LOG=$D/ab-$(date +%Y%m%d-%H%M%S).txt
echo "A/B 对照  每轮 ${WIN}s × ${ROUNDS} 轮  交替进行" | tee $LOG
echo "  A = $A_DESC" | tee -a $LOG
echo "  B = $B_DESC" | tee -a $LOG
echo "" | tee -a $LOG

: > $D/_abA; : > $D/_abB

i=1
while [ $i -le $ROUNDS ]; do
  # --- A ---
  if check_precond > /dev/null; then
    sh -c "$A_CMD" >/dev/null 2>&1
    sleep 5
    R=$(measure_once $WIN)
    MA=$(echo "$R" | cut -f1); FR=$(echo "$R" | cut -f2)
    echo "  轮$i A: ${MA} mA  失败率 ${FR}%" | tee -a $LOG
    echo "$MA" >> $D/_abA
  fi

  # --- B ---
  if check_precond > /dev/null; then
    sh -c "$B_CMD" >/dev/null 2>&1
    sleep 5
    R=$(measure_once $WIN)
    MA=$(echo "$R" | cut -f1); FR=$(echo "$R" | cut -f2)
    echo "  轮$i B: ${MA} mA  失败率 ${FR}%" | tee -a $LOG
    echo "$MA" >> $D/_abB
  fi

  i=$((i + 1))
done

[ -n "$RESTORE" ] && sh -c "$RESTORE" >/dev/null 2>&1

MA_medA=$(median_of < $D/_abA)
MA_medB=$(median_of < $D/_abB)
nA=$(wc -l < $D/_abA)
nB=$(wc -l < $D/_abB)

echo "" | tee -a $LOG
echo "===== 结论 =====" | tee -a $LOG
echo "  A 中位数: ${MA_medA} mA  (n=$nA)" | tee -a $LOG
echo "  B 中位数: ${MA_medB} mA  (n=$nB)" | tee -a $LOG

if [ "$MA_medA" != "n/a" ] && [ "$MA_medB" != "n/a" ]; then
  awk -v a="$MA_medA" -v b="$MA_medB" 'BEGIN{
    d = b - a
    pct = (a>0) ? d*100/a : 0
    printf "  差值: %+.1f mA (%+.0f%%)\n", d, pct
    # 噪声经验值：本机同条件重复测量的波动约 ±15%
    if (pct < -15)      print "  => B 明显更省电，建议采用"
    else if (pct > 15)  print "  => B 明显更费电，不要采用"
    else                print "  => 差异在噪声范围内，无结论（需增大轮数或延长时间）"
  }' | tee -a $LOG
fi

rm -f $D/_abA $D/_abB
echo "" | tee -a $LOG
echo "日志: $LOG" | tee -a $LOG
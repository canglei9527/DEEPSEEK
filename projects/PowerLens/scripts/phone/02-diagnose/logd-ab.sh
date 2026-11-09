#!/system/bin/sh
# logd-ab.sh -- 验证 logd 高耗电是否由我的采集导致
#
# 方法：
#   阶段 A（采集开启）：让 plc-daemon 正常跑，测 logd 的 CPU 增速
#   阶段 B（采集停止）：杀掉 plc-daemon，测同样时长的 logd 增速
#   对比两者，即可判断采集贡献了多少
#
# 用法: su -c "sh /data/local/tmp/logd-ab.sh [每段秒数，默认180]"

WIN=${1:-180}
LOG=/data/local/tmp/logd-ab.log
D=/data/local/tmp/plc

: > $LOG
echo "===== logd A/B $(date '+%F %T') 每段 ${WIN}s =====" >> $LOG

logd_cpu() {
  P=$(pidof logd)
  [ -z "$P" ] && { echo "-1"; return; }
  awk '{print $14+$15}' /proc/$P/stat 2>/dev/null
}

logd_io() {
  P=$(pidof logd)
  [ -z "$P" ] && { echo "0 0"; return; }
  awk '{print $1, $2}' /proc/$P/io 2>/dev/null
}

dmesg_cnt() {
  wc -l < /dev/kmsg 2>/dev/null || echo 0
}

run_phase() {
  local label=$1 win=$2
  local t0 c0 i0r i0w c1 i1r i1w t1 dt dticks
  c0=$(logd_cpu)
  read i0r i0w <<EOF
$(logd_io)
EOF
  t0=$(date +%s)
  sleep $win
  c1=$(logd_cpu)
  read i1r i1w <<EOF
$(logd_io)
EOF
  t1=$(date +%s)
  dt=$((t1 - t0))
  [ $dt -lt 1 ] && dt=$win
  dticks=$((c1 - c0))
  awk -v lbl="$label" -v dt=$dt -v d=$dticks -v r0=$i0r -v r1=$i1r -v w0=$i0w -v w1=$i1w 'BEGIN{
    printf "  [%s] %ds  logd CPU +%d ticks = %d ms  写入 %d KB  读 %d KB\n", \
      lbl, dt, d, d*10, (w1-w0)/1024, (r1-r0)/1024
  }' | tee -a $LOG
}

echo "" >> $LOG
echo "== 阶段 A：采集开启（plc-daemon 运行中）==" >> $LOG
N=$(ps -A -o ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | wc -l)
echo "  守护实例数: $N" >> $LOG
run_phase "A:采集开" $WIN

echo "" >> $LOG
echo "== 阶段 B：停止采集 ==" >> $LOG
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null
  echo "  killed 守护 $P" >> $LOG
done
sleep 5
run_phase "B:采集停" $WIN

echo "" >> $LOG
echo "== 阶段 C：恢复采集 ==" >> $LOG
setsid sh "$D/plc-daemon.sh" 60 1200 >/dev/null 2>&1 &
sleep 5
N2=$(ps -A -o ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | wc -l)
echo "  守护实例数: $N2" >> $LOG
run_phase "C:采集开(恢复)" $WIN

echo "" >> $LOG
echo "===== 结论 =====" >> $LOG
echo "  若 A 明显高于 B，则 logd 开销主要由采集（dumpsys）导致" >> $LOG
echo "  若 A ≈ B，则 logd 与采集无关，另有原因" >> $LOG

cat $LOG
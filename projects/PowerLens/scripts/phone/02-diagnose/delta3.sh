#!/system/bin/sh
# delta3.sh -- per-UID CPU 差值（纯 awk 合并，不用 join；busybox join 排序语义对不上）
# 用法: su -c "sh /data/local/tmp/delta3.sh 90"

GAP=${1:-90}
D=/data/local/tmp/plc
mkdir -p $D

snap() {
  cat /proc/uid_cputime/show_uid_stat | sed 's/: / /' | awk '{print $1, $2+$3}' > "$1"
}
snap $D/c0.txt
cat /proc/uid_io/stats > $D/io0.txt
C0=$(cat /sys/class/power_supply/battery/charge_counter)
T0=$(date +%s)

echo "等待 ${GAP}s ..."
sleep $GAP

snap $D/c1.txt
cat /proc/uid_io/stats > $D/io1.txt
C1=$(cat /sys/class/power_supply/battery/charge_counter)
T1=$(date +%s)
DT=$((T1 - T0)); [ $DT -le 0 ] && DT=$GAP

echo
echo "########## 区间总览（${DT}s）##########"
echo "  电量计: $C0 -> $C1  ($(( (C0-C1)/1000 )) mAh)"
awk -v a=$C0 -v b=$C1 -v t=$DT 'BEGIN{printf "  平均电流: %.1f mA\n", (a-b)/1000*3600/t}'

# 纯 awk 合并两个快照
awk -v dt=$DT '
FNR==NR { prev[$1] = $2; next }
{
  uid = $1
  if (uid in prev) {
    d = $2 - prev[uid]
    if (d > 0) printf "%d %d\n", uid, d
  }
}
' $D/c0.txt $D/c1.txt | sort -k2,2nr > $D/delta.txt

TOTAL=$(awk '{s+=$2} END{print s+0}' $D/delta.txt)
COUNT=$(wc -l < $D/delta.txt)

echo
echo "########## CPU 增量 top 25 ##########"
head -n 25 $D/delta.txt | while read uid d; do
  pkg=""
  [ -r /data/system/packages.list ] && pkg=$(awk -v u="$uid" '$2==u{print $1; exit}' /data/system/packages.list)
  if [ -z "$pkg" ]; then
    case $uid in
      0) pkg="root/内核" ;;
      1000) pkg="system_server" ;;
      1001) pkg="radio/电话" ;;
      1013) pkg="media" ;;
      1021) pkg="NFC" ;;
      1047) pkg="wifi" ;;
      1068) pkg="camera" ;;
      1999) pkg="shell" ;;
      *) pkg="(uid $uid)" ;;
    esac
  fi
  ms=$((d / 1000))
  pct=$((d * 100 / (DT * 1000000)))
  printf '  %-42s %8d ms  %4d%%\n' "$pkg" "$ms" "$pct"
done

echo
awk -v t="$TOTAL" -v n="$COUNT" -v dt=$DT 'BEGIN{
  printf "  合计 %d 个 UID，%.1f 秒 CPU = 单核 %.0f%%\n", n, t/1000000, t/(dt*1000000)*100
}'

echo
echo "########## 进程/线程 ##########"
P=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l)
T=0
for d in /proc/[0-9]*; do
  set -- "$d"/task/*
  T=$((T + $#))
done
echo "  processes=$P  threads=$T"
echo "  loadavg: $(cat /proc/loadavg)"

echo
echo "########## /proc/stat 5 秒差值 ##########"
awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $4, $5, $6}' /proc/stat > $D/s0.txt
sleep 5
awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8+$9, $4, $5, $6}' /proc/stat > $D/s1.txt
awk '
FNR==NR { t1=$1; i1=$2; w1=$3; q1=$4; next }
{
  dt = $1 - t1; di = $2 - i1; dw = $3 - w1; dq = $4 - q1
  if (dt > 0) {
    busy = dt - di
    printf "  总 jiffies=%d  idle=%d  iowait=%d  irq=%d\n", dt, di, dw, dq
    printf "  非idle = %.1f%%（8 核合计）=> 单核等效 %.1f%%\n", busy*100/dt, busy*100/dt/8
  }
}' $D/s0.txt $D/s1.txt
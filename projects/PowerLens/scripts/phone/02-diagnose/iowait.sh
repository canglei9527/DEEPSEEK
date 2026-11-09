#!/system/bin/sh
# iowait.sh -- iowait 92% 的真凶定位
# 用法: su -c "sh /data/local/tmp/iowait.sh"
#
# 关键判据：
#   zram 读 = swap in（从压缩内存解压回 RAM）→ 消耗 CPU，但表现为 iowait 吗？
#   实际上 zram 的 I/O 计在 zram0 块设备上，wchan 会是 zram 相关的锁。
#   f2fs /data 写 = 真闪存 I/O → 真 iowait。
#   必须分清是哪一个。

echo "########## 1. zram 状态 ##########"
cat /proc/swaps | sed 's/^/  /'
echo -n '  zram0 mm_stat: '; cat /sys/block/zram0/mm_stat 2>/dev/null
echo "  (orig_data  compr_data  mem_used  ...  same_pages  pages_compacted ...)"
awk '{printf "  zram: 原始=%.2f GB  压缩=%.2f GB  实际内存=%.2f GB  压缩比=%.2f\n", $1/1073741824, $2/1073741824, $3/1073741824, $1/$3}' /sys/block/zram0/mm_stat 2>/dev/null

echo
echo "########## 2. 15 秒 I/O 归因：zram vs /data ##########"
z0=$(awk '/zram0/{print $6, $10}' /proc/diskstats)
d0=$(awk '$3=="dm-55"{print $6, $10}' /proc/diskstats)
s0=$(awk '/^cpu /{print $5}' /proc/stat)
sleep 15
z1=$(awk '/zram0/{print $6, $10}' /proc/diskstats)
d1=$(awk '$3=="dm-55"{print $6, $10}' /proc/diskstats)
s1=$(awk '/^cpu /{print $5}' /proc/stat)

echo "$z0 $z1 $d0 $d1 $s0 $s1" | awk '{
  zr=($3-$1)*512/1048576; zw=($4-$2)*512/1048576
  dr=($7-$5)*512/1048576; dw=($8-$6)*512/1048576
  printf "  zram0  读 %.2f MB  写 %.2f MB\n", zr, zw
  printf "  /data  读 %.2f MB  写 %.2f MB\n", dr, dw
  printf "  iowait 增量: %d jiffies (= %.1f 秒)\n", $9-$s1, ($9-$s1)/100
}'

echo
echo "########## 3. swap in/out 速率（/proc/vmstat） ##########"
a=$(grep -E '^pswpin |^pswpout |^pgmajfault |^pgpgin |^pgpgout ' /proc/vmstat | awk '{print $2}' | tr '\n' ' ')
sleep 15
b=$(grep -E '^pswpin |^pswpout |^pgmajfault |^pgpgin |^pgpgout ' /proc/vmstat | awk '{print $2}' | tr '\n' ' ')
echo "$a $b" | awk '{
  printf "  pswpin  (换入页) = %d  (%.1f 页/秒)\n", $6-$1, ($6-$1)/15
  printf "  pswpout (换出页) = %d  (%.1f 页/秒)\n", $7-$2, ($7-$2)/15
  printf "  pgmajfault       = %d  (%.1f 次/秒)\n", $8-$3, ($8-$3)/15
  printf "  pgpgin           = %d  (%.1f 页/秒)\n", $9-$4, ($9-$4)/15
  printf "  pgpgout          = %d  (%.1f 页/秒)\n", $10-$5, ($10-$5)/15
}'

echo
echo "########## 4. 内存压力（真实值） ##########"
grep -E 'MemTotal|MemFree|MemAvailable|Cached:|SwapTotal|SwapFree|AnonPages' /proc/meminfo | sed 's/^/  /'
echo "  --- PSI ---"
cat /proc/pressure/io 2>/dev/null | sed 's/^/  io: /'
cat /proc/pressure/memory 2>/dev/null | sed 's/^/  mem: /'

echo
echo "########## 5. 谁在制造 major fault（真 I/O） ##########"
for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  [ -r "$p/stat" ] || continue
  awk -v pid=$pid '{print pid, $12+$13+$14+$15}' "$p/stat" 2>/dev/null
done > /tmp/mf0
sleep 15
for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  [ -r "$p/stat" ] || continue
  awk -v pid=$pid '{print pid, $12+$13+$14+$15}' "$p/stat" 2>/dev/null
done > /tmp/mf1

awk 'FNR==NR{p[$1]=$2;next}{
  if ($1 in p) { d=$2-p[$1]; if (d>0) printf "%d %d\n", $1, d }
}' /tmp/mf0 /tmp/mf1 | sort -k2 -nr | head -n 15 | while read pid d; do
  nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | head -c 44)
  [ -z "$nm" ] && nm=$(cat /proc/$pid/comm 2>/dev/null)
  printf '  pid=%-7s %6d faults/15s  %s\n' "$pid" "$d" "$nm"
done

echo
echo "########## 6. 8 核各自负载（是否某核被 D 态卡死） ##########"
awk '/^cpu[0-9] /{printf "%s %d %d\n", $1, $2+$3+$4+$5+$6+$7+$8, $5}' /proc/stat > /tmp/pc0
sleep 10
awk '/^cpu[0-9] /{printf "%s %d %d\n", $1, $2+$3+$4+$5+$6+$7+$8, $5}' /proc/stat > /tmp/pc1
awk 'FNR==NR{t[$1]=$2;i[$1]=$3;next}{
  if ($1 in t) {
    dt=$2-t[$1]; di=$3-i[$1]
    if (dt>0) printf "  %-6s busy=%.0f%%  iowait=%.0f%%\n", $1, (dt-di)*100/dt, di*100/dt
  }
}' /tmp/pc0 /tmp/pc1

echo
echo "########## 7. 那几个 D 态内核线程的 wchan ##########"
for pid in 165 609 907 908 911 930 931 932 933 936 937 938 939 940; do
  if [ -d /proc/$pid ]; then
    nm=$(cat /proc/$pid/comm 2>/dev/null)
    w=$(cat /proc/$pid/wchan 2>/dev/null)
    st=$(awk '{print $3}' /proc/$pid/stat 2>/dev/null)
    printf '  pid=%-6s %-6s wchan=%-30s %s\n' "$pid" "$st" "$w" "$nm"
  fi
done
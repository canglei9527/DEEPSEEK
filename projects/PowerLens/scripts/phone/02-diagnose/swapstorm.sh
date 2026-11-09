#!/system/bin/sh
# swapstorm.sh -- zram 换页风暴定位
#
# 实测数据：
#   pswpin  7041 页/秒（换入）
#   pswpout 3175 页/秒（换出）
#   pgmajfault 192 次/秒
#   iowait 92%，user+sys 仅 2.3%
#   zram 原始 9.04 GB / 压缩 3.08 GB / 占内存 2.49 GB
#   MemFree 779 MB，MemAvailable 3.87 GB
#
# 关键疑问：MemAvailable 还有 3.87 GB，为什么还在疯狂 swap？
#   可能是 zram 写满（16 GB 里用了 9.5 GB）触发了 aggressive reclaim，
#   或者 watermarks 设置过于激进。
#
# 用法: su -c "sh /data/local/tmp/swapstorm.sh"

echo "########## 1. vm 参数（决定换页激进度） ##########"
for f in swappiness min_free_kbytes watermark_scale_factor watermark_boost_factor \
         page-cluster vfs_cache_pressure overcommit_ratio extfrag_threshold \
         dirty_ratio dirty_background_ratio; do
  printf '  vm.%-26s %s\n' "$f" "$(cat /proc/sys/vm/$f 2>/dev/null)"
done

echo
echo "  --- 内存水位 ---"
grep -E 'min|low|high|protection' /proc/zoneinfo 2>/dev/null | head -n 20 | sed 's/^/  /'

echo
echo "########## 2. zram 是否接近写满 ##########"
cat /sys/block/zram0/mm_stat 2>/dev/null | awk '{
  printf "  原始数据      : %.2f GB\n", $1/1073741824
  printf "  压缩后        : %.2f GB\n", $2/1073741824
  printf "  实际占用物理内存: %.2f GB\n", $3/1073741824
  printf "  mem_limit     : %s\n", ($4==0 ? "无限制" : $4/1073741824 " GB")
  printf "  压缩比        : %.2f\n", $1/$3
  printf "  不可压缩页    : %d\n", $8
}'
echo "  --- disksize / 使用率 ---"
ds=$(cat /sys/block/zram0/disksize 2>/dev/null)
cat /proc/swaps | tail -n1 | awk -v ds=$ds '{printf "  swap 已用 %.2f GB / %.2f GB = %.1f%%\n", $4/1048576, $3/1048576, $4*100/$3}'

echo
echo "########## 3. 内存大头（RSS + Swap 合计） ##########"
awk '
/^Name:/{nm=$2}
/^VmRSS:/{rss=$2}
/^VmSwap:/{sw=$2; printf "%d\t%s\t%d\n", rss+sw, nm, sw}
' /proc/[0-9]*/status 2>/dev/null | sort -nr | head -n 20 | \
while IFS="$(printf '\t')" read tot nm sw; do
  printf '  %-40s 合计 %7.1f MB (swap %6.1f MB)\n' "$nm" "$(awk -v t=$tot 'BEGIN{print t/1024}')" "$(awk -v s=$sw 'BEGIN{print s/1024}')"
done

echo
echo "########## 4. 谁在疯狂缺页（10 秒增量） ##########"
for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  awk -v pid=$pid '{print pid, $10+$12}' "$p/stat" 2>/dev/null
done > /tmp/f0
sleep 10
for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  awk -v pid=$pid '{print pid, $10+$12}' "$p/stat" 2>/dev/null
done > /tmp/f1
awk 'FNR==NR{p[$1]=$2;next}{if($1 in p){d=$2-p[$1]; if(d>500) printf "%d %d\n",$1,d}}' /tmp/f0 /tmp/f1 \
  | sort -k2 -nr | head -n 12 | while read pid d; do
      nm=$(tr -d '\000' < /proc/$pid/cmdline 2>/dev/null | head -c 40)
      [ -z "$nm" ] && nm=$(cat /proc/$pid/comm 2>/dev/null)
      printf '  pid=%-7s %7d faults/10s  %s\n' "$pid" "$d" "$nm"
    done

echo
echo "########## 5. kswapd / kcompactd 活跃度 ##########"
ps -A -o PID,STAT,NAME 2>/dev/null | grep -E 'kswapd|kcompactd' | sed 's/^/  /'
echo "  --- vmstat 累计 ---"
grep -E 'pgscan_kswapd|pgscan_direct|pgsteal_kswapd|pgsteal_direct|compact_stall|allocstall' /proc/vmstat 2>/dev/null | sed 's/^/  /'

echo
echo "########## 6. f2fs /data 的 GC 状态 ##########"
if [ -r /sys/fs/f2fs/dm-55/gc_urgent ]; then
  for f in gc_urgent gc_urgent_sleep_time gc_min_sleep_time gc_max_sleep_time \
           dirty_segments free_segments gc_mode; do
    printf '  f2fs.%-24s %s\n' "$f" "$(cat /sys/fs/f2fs/dm-55/$f 2>/dev/null)"
  done
fi
echo "  --- segment info ---"
SEG=/sys/fs/f2fs/dm-55/segment_info
if [ -r "$SEG" ]; then
  echo "  segment_info 行数: $(wc -l < "$SEG")"
else
  echo "  (不可读)"
fi

echo
echo "########## 7. 结论性判断 ##########"
FREE=$(grep MemFree /proc/meminfo | awk '{print $2}')
AVAIL=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
SWAPU=$(cat /proc/swaps | tail -n1 | awk '{print $4}')
SWAPT=$(cat /proc/swaps | tail -n1 | awk '{print $3}')
echo "  MemFree=${FREE}kB  MemAvailable=${AVAIL}kB"
echo "  swap 用 ${SWAPU}kB / ${SWAPT}kB"
awk -v f=$FREE -v a=$AVAIL -v su=$SWAPU -v st=$SWAPT 'BEGIN{
  if (st>0 && su*100/st > 55) print "  => swap 使用率超过 55%，已进入高压换页区"
  if (a < 1500000) print "  => MemAvailable 低于 1.5 GB，reclaim 会非常激进"
  if (f < 300000) print "  => MemFree 长期低于 300 MB，kswapd 会持续工作"
}'
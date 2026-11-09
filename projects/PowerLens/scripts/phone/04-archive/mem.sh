#!/system/bin/sh
# mem.sh -- 内存压力 / zram / swap 真相（这台 15 Pro 只有 15 GB，支付宝一个占 1.3 GB）
# 用法: su -c "sh /data/local/tmp/mem.sh"

echo "===== MEMINFO ====="
grep -E 'MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree|Dirty|Writeback|AnonPages|Shmem|SReclaimable|Slab' /proc/meminfo

echo
echo "===== PSI (pressure stall information) ====="
for f in cpu memory io; do
    [ -r /proc/pressure/$f ] && { echo "--- $f"; cat /proc/pressure/$f; }
done

echo
echo "===== zram ====="
for z in /sys/block/zram*/; do
    echo "--- $z"
    for f in disksize orig_data_size compr_data_size mem_used_total mem_limit; do
        [ -r "$z$f" ] && printf '  %-20s %s\n' "$f" "$(cat $z$f)"
    done
    [ -r "$z/mm_stat" ] && { echo -n '  mm_stat: '; cat $z/mm_stat; }
done
swapon --show 2>/dev/null || cat /proc/swaps

echo
echo "===== 各进程 swap 占用 top 12 ====="
printf '%-8s %-10s %s\n' PID SWAP_KB NAME
for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    s=$(awk '/^VmSwap:/{print $2}' "$p/status" 2>/dev/null)
    [ -z "$s" ] && continue
    [ "$s" -lt 2048 ] && continue
    cmd=$(tr -d '\0' < "$p/cmdline" 2>/dev/null | cut -c1-55)
    [ -z "$cmd" ] && cmd=$(awk '{print $2}' "$p/stat" 2>/dev/null | tr -d '()')
    echo "$pid $s $cmd"
done | sort -k2 -nr | head -n 12

echo
echo "===== RSS top 12 合计 ====="
tot=0
for p in /proc/[0-9]*; do
    r=$(awk '{print $2}' "$p/statm" 2>/dev/null); [ -z "$r" ] && continue
    tot=$((tot + r*4/1024))
done
echo "所有进程 RSS 之和: ${tot} MB"

echo
echo "===== lmkd 杀进程记录 ====="
dmesg 2>/dev/null | grep -iE 'lowmemorykiller|lmkd|kill.*adj|oom' | tail -n 10

echo
echo "===== vm 参数 ====="
for f in swappiness min_free_kbytes watermark_scale_factor page-cluster; do
    v=$(cat /proc/sys/vm/$f 2>/dev/null)
    printf 'vm.%-26s %s\n' "$f" "$v"
done

echo
echo "===== 支付宝相关进程数量 ====="
ps -A -o PID,NAME 2>/dev/null | grep -c AlipayGphone
ps -A -o PID,NAME 2>/dev/null | grep AlipayGphone | head -n 20
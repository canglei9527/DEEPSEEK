#!/system/bin/sh
# free-ram.sh -- 彻底清理内存，防 LMK 杀 adbd
# 用法: sh free-ram.sh

echo "before:"
grep -E "MemTotal|MemFree|MemAvailable" /proc/meminfo

# 1. 清所有后台应用（包括系统应用）
for p in $(pm list packages -3 | cut -d: -f2); do
  am force-stop "$p" 2>/dev/null
done
# 也清一些系统大户
for p in com.miui.home com.android.systemui com.tencent.mm com.tencent.mobileqq com.android.vending; do
  am force-stop "$p" 2>/dev/null
done

# 2. 清缓存（释放 page cache）
echo 3 > /proc/sys/vm/drop_caches

# 3. 降低 swappiness（减少 swap 使用，防 LMK 激进）
echo 10 > /proc/sys/vm/swappiness 2>/dev/null

sleep 2
echo "after:"
grep -E "MemTotal|MemFree|MemAvailable" /proc/meminfo
cat /proc/loadavg

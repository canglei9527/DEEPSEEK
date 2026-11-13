#!/system/bin/sh
# check-traffic.sh -- 检查 UID 0/1000 的流量字段是否异常
# 用法: su -c "sh /data/local/tmp/check-traffic.sh"

F=/data/data/com.powerlens.app/files/snapshots.tsv

echo "===== 1. uid 0 最近 3 条（列: ts uid pkg cpu cpuOff wl wlOff rx tx ...）====="
awk -F'\t' '$2==0' "$F" | tail -n 3 | sed 's/^/  /'

echo
echo "===== 2. uid 1000 最近 3 条 ====="
awk -F'\t' '$2==1000' "$F" | tail -n 3 | sed 's/^/  /'

echo
echo "===== 3. 全库 rx/tx 最大的前 8 条 ====="
awk -F'\t' 'NF>9 && $2!="*" { printf "%s\tuid=%s\t%s\trx=%s\ttx=%s\n", $1, $2, $3, $8, $9 }' "$F" \
  | sort -t'=' -k4 -nr | head -n 8 | sed 's/^/  /'

echo
echo "===== 4. uid 0 的 rx/tx 时间序列（看是否有突增）====="
awk -F'\t' '$2==0 { printf "  ts=%s rx=%s tx=%s\n", substr($1,6), $8, $9 }' "$F" | tail -n 12

echo
echo "===== 5. 系统侧真实值对照 ====="
echo "  /proc/uid_io/stats 里 uid 0:"
awk '$1==0' /proc/uid_io/stats 2>/dev/null | sed 's/^/    /'
echo "  说明：第2列=rchar(读字符) 第3列=wchar(写字符)"
echo "        这两个是「读写字节数」，包含文件 IO，不是网络流量！"
echo
echo "  /proc/uid_io/stats 里 uid 1000:"
awk '$1==1000' /proc/uid_io/stats 2>/dev/null | sed 's/^/    /'

echo
echo "===== 6. 结论 ====="
echo "  如果 uid 0 的 rx/tx 很大，是因为 C 采集器读的是 /proc/uid_io/stats 的"
echo "  rchar/wchar，那是【文件读写字节数】，不是网络流量。"
echo "  dumpsys batterystats 里的 wifi=/mobile= 才是网络流量。"
echo "  两者混在同一个 rx/tx 字段里显示为「流量」是错的。"
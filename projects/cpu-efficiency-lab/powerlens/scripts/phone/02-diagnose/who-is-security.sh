#!/system/bin/sh
# who-is-security.sh -- 查清「安全」这个条目到底是什么，以及它何时出现
# 用法: su -c "sh /data/local/tmp/who-is-security.sh"

D=/data/local/tmp/powerlens
SNAP=$D/snapshots.tsv
DIMS=$D/dims.tsv

echo "########## 1. "安全" 对应的 UID ##########"
echo "  --- 当前 C 采集器给它什么名字 ---"
grep -o 'sys:security' $SNAP 2>/dev/null | head -n1
echo "  --- uid 1041 在 packages.list 里 ---"
awk '$2==1041{print "    "$0}' /data/system/packages.list 2>/dev/null

echo ""
echo "########## 2. 所有与安全/security 相关的 UID ##########"
awk '$1 ~ /security|Security|guard|Guard|avast|360|kingsoft|cleaner/ {print "  uid="$2"  "$1}' /data/system/packages.list 2>/dev/null | head -n 20

echo ""
echo "########## 3. 当前运行的、名字带 security 的进程 ##########"
ps -A -o PID,NAME,ARGS 2>/dev/null | grep -iE 'security|secenter|guard' | grep -v grep | head -n 15 | sed 's/^/  /'

echo ""
echo "########## 4. miui.securitycenter 相关进程 ##########"
ps -A -o PID,USER,ARGS 2>/dev/null | grep -i 'miui' | grep -v grep | head -n 10 | sed 's/^/  /'

echo ""
echo "########## 5. "安全" 在 snapshots 里首次出现的时间 ##########"
echo "  --- 找所有 pkg 为 sys:security 的行 ---"
grep -P '\tsys:security\t' $SNAP 2>/dev/null | head -n 3 | cut -f1,2 | while read ts uid; do
  echo "    ts=$ts uid=$uid  = $(date -d @$((ts/1000)) '+%F %T' 2>/dev/null || echo $ts)"
done
echo "  --- 该行总数 ---"
grep -cP '\tsys:security\t' $SNAP 2>/dev/null

echo ""
echo "########## 6. 数据里所有出现过的系统 UID 名称 ##########"
cut -f3 $SNAP 2>/dev/null | grep '^sys:' | sort | uniq -c | sort -nr | sed 's/^/  /'

echo ""
echo "########## 7. 时间线：每小时有哪些 sys: 条目 ##########"
awk -F'\t' '$3 ~ /^sys:/ {
  h = int(($1/1000 % 86400) / 3600) + 8
  if (h >= 24) h -= 24
  print h, $3
}' $SNAP 2>/dev/null | sort -u | awk '{a[$1]=a[$1]" "$2} END{for(h in a) print "  "h"时:"a[h]}' | sort -n | head -n 20

echo ""
echo "########## 8. 当前 UI 显示的 "安全" 是哪个 UID ##########"
echo "  --- SYS_LABEL 里 sys:security 对应 ---"
echo "    SYS_LABEL.put(\"sys:security\", \"安全\")  →  uid 1041"
echo "  --- uid 1041 的 procfs 数据 ---"
awk -F'\t' '$2==1041' $SNAP 2>/dev/null | tail -n 2 | cut -f1,2,3,4

echo ""
echo "########## 9. 结论指引 ##########"
echo "  如果第 3/4 节显示 miui.securitycenter 或类似进程在跑，"
echo "  且第 5 节显示 sys:security 最近才出现，"
echo "  那么「安全」就是小米安全中心服务被唤醒后开始吃 CPU。"
echo ""
echo "  这可能是："
echo "    ① 系统扫描（病毒库更新/应用扫描）—— 正常，会自己结束"
echo "    ② 你装了某个应用触发了安全扫描"
echo "    ③ miui.securitycenter 卡在循环里 —— 需要处理"
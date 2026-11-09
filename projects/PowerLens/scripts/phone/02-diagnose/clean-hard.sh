#!/system/bin/sh
# clean-hard.sh -- 硬清洗：只保留最后 N 个健康批次
#
# 上一版 clean-perfect.sh 的 bug：
#   它用「第一个健康批次的时间戳」作起点，但 awk 输出是字符串，
#   排序和比较都不可靠，导致起点算早了。
#
# 这版做法：
#   1. 从文件尾部倒序扫描，找到第一个「问号率 < 10%」的批次
#   2. 从该批次起全部保留
#   3. 用 tail 逐行方式，不依赖排序
#
# 用法: su -c "sh /data/local/tmp/clean-hard.sh [保留批次数，默认无限]"

PKG=com.powerlens.app
F=/data/data/$PKG/files/snapshots.tsv
D=/data/local/tmp/plc

[ -f "$F" ] || { echo "无数据文件"; exit 1; }

BAK="$D/snapshots-hard-$(date +%Y%m%d-%H%M%S).tsv"
cp -f "$F" "$BAK" && echo "已备份: $BAK"
BEFORE=$(wc -l < "$F")
echo "原行数: $BEFORE"

# 用 awk 一次性算出「每个时间戳的问号率」，按数值排序写文件
awk -F'\t' '
  $2 != "*" { cnt[$1]++; if ($3=="?") qm[$1]++ }
  $2 == "*" && $4 > 100000 { bat[$1]=1 }
  END { for (t in cnt) printf "%d %d %d %d\n", t+0, cnt[t], qm[t]+0, (t in bat)?1:0 }
' "$F" | sort -n > /tmp/b.txt

echo
echo "===== 批次健康度（末 12 批）====="
tail -n 12 /tmp/b.txt | while read ts cnt qm isbat; do
  pct=0
  [ "$cnt" -gt 0 ] && pct=$((qm * 100 / cnt))
  echo "  $ts  uid=$cnt  问号=$qm (${pct}%)  电池=$([ "$isbat" = "1" ] && echo µAh || echo 非µAh)"
done

# 找最后一个「问号率 >= 10%」的批次，从它的下一批开始保留
BAD_TS=$(awk '{ pct = ($2>0)?$3*100/$2:100; if (pct >= 10) last=$1 } END { print last }' /tmp/b.txt)
echo
echo "  最后一个不健康批次: ${BAD_TS:-无}"

if [ -z "$BAD_TS" ]; then
  echo "  全部批次都健康，无需清洗"
  exit 0
fi

# 取它的下一条时间戳作为起点
START=$(awk -v b="$BAD_TS" '$1 > b { print $1; exit }' /tmp/b.txt)
if [ -z "$START" ]; then
  echo "  [问题] 不健康批次是最后一批，没有可保留的数据"
  echo "  解决办法：等下一次采集（C 守护每 10 分钟一次），或手动触发："
  echo "    /data/local/tmp/plc/plcollect --once -f $F"
  exit 1
fi
echo "  保留起点: $START"

awk -F'\t' -v s="$START" '$1+0 >= s+0' "$F" > "$F.new"
AFTER=$(wc -l < "$F.new")
echo "  $BEFORE -> $AFTER 行"

if [ "$AFTER" -lt 300 ]; then
  echo "  [警告] 太少（$AFTER），放弃"
  rm -f "$F.new"; exit 1
fi

mv "$F.new" "$F"

APPUID=$(stat -c '%u' "/data/data/$PKG/files" 2>/dev/null)
[ -n "$APPUID" ] && { chown "$APPUID:$APPUID" "$F" 2>/dev/null; chmod 644 "$F" 2>/dev/null; }
CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
[ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null

echo
echo "===== 清洗后 ====="
echo "  行数: $(wc -l < $F)"
T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
PCT=0; [ "$T" -gt 0 ] && PCT=$((Q * 100 / T))
echo "  问号: $Q / $T = ${PCT}%"
echo "  唯一包名: $(awk -F'\t' '$2!="*" && $3!="?" {print $3}' "$F" | sort -u | wc -l)"
echo "  --- 电池行 ---"
awk -F'\t' '$2=="*" { if($4>100000) print "µAh"; else if($4>0) print "mAh"; else print "空" }' "$F" | sort | uniq -c | sed 's/^/    /'
echo "  --- 跨度 ---"
awk -F'\t' '$2=="*" && $4>100000 { if(!f) f=$1; l=$1 } END { if(f) printf "    %.2f 小时\n", (l-f)/3600000 }' "$F"

echo
echo "===== 核算 ====="
"$D/plcollect" --status -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "===== 重启 App ====="
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "  已重启"
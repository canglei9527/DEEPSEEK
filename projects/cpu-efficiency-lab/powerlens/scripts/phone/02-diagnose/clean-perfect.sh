#!/system/bin/sh
# clean-perfect.sh -- 精确清洗：只保留「新采集器产出」的快照批次
#
# 判定标准（同时满足）：
#   1. 该批次电池行是 µAh 格式（$4 > 100000）
#   2. 该批次 pkg=? 的比例 < 30%（说明用的是 packages.list 补全的新版）
#
# 做法：
#   按时间戳分组统计每批的「UID 行数」和「问号行数」，
#   找出从哪一批开始 pkg 补全率达标，从那里起全部保留。
#
# 用法: su -c "sh /data/local/tmp/clean-perfect.sh"

PKG=com.powerlens.app
F=/data/data/$PKG/files/snapshots.tsv
D=/data/local/tmp/plc

[ -f "$F" ] || { echo "无数据文件"; exit 1; }

BAK="$D/snapshots-before-perfect-$(date +%Y%m%d-%H%M%S).tsv"
cp -f "$F" "$BAK" && echo "已备份: $BAK"
BEFORE=$(wc -l < "$F")
echo "原行数: $BEFORE"

echo
echo "===== 每批次健康度（时间戳 / UID行数 / 问号数 / 电池格式）====="
awk -F'\t' '
  $2 != "*" {
    cnt[$1]++;
    if ($3 == "?") qm[$1]++;
  }
  $2 == "*" {
    if ($4 > 100000) bat[$1] = "uAh";
    else if ($4 > 0) bat[$1] = "mAh";
    else             bat[$1] = "empty";
  }
  END {
    for (t in cnt) {
      printf "%s\t%d\t%d\t%s\n", t, cnt[t], qm[t]+0, (t in bat ? bat[t] : "?");
    }
  }
' "$F" | sort -n > /tmp/batches.txt

# 打印最近 20 批
tail -n 20 /tmp/batches.txt | awk -F'\t' '{
  pct = ($2>0) ? $3*100/$2 : 0
  printf "  %s  uid行=%-4d 问号=%-4d (%2.0f%%) 电池=%s\n", substr($1,6), $2, $3, pct, $4
}'

echo
echo "===== 找出第一个「健康」批次（µAh 且 问号<30%）====="
GOOD_TS=$(awk -F'\t' '{
  pct = ($2>0) ? $3*100/$2 : 100
  if ($4=="uAh" && pct < 30) { print $1; exit }
}' /tmp/batches.txt)

if [ -z "$GOOD_TS" ]; then
  echo "  [未找到] 说明当前数据里没有完全健康的批次"
  echo "  改用「最后一个 µAh 批次」作为起点"
  GOOD_TS=$(awk -F'\t' '$4=="uAh"{t=$1} END{print t}' /tmp/batches.txt)
fi

echo "  健康批次起点: $GOOD_TS"

[ -z "$GOOD_TS" ] && { echo "无法确定起点，放弃"; exit 1; }

echo
echo "===== 执行清洗 ====="
awk -F'\t' -v s="$GOOD_TS" '$1+0 >= s+0' "$F" > "$F.new"
AFTER=$(wc -l < "$F.new")
echo "  $BEFORE -> $AFTER 行"

if [ "$AFTER" -lt 200 ]; then
  echo "  [警告] 保留行数太少（$AFTER），放弃"
  rm -f "$F.new"
  exit 1
fi

mv "$F.new" "$F"

APPUID=$(stat -c '%u' "/data/data/$PKG/files" 2>/dev/null)
if [ -n "$APPUID" ]; then
  chown "$APPUID:$APPUID" "$F" 2>/dev/null
  chmod 644 "$F" 2>/dev/null
  CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
  [ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null
fi

echo
echo "===== 清洗后 ====="
echo "  行数: $(wc -l < $F)"
echo "  --- 电池行 ---"
awk -F'\t' '$2=="*" {
  if ($4>100000) print "    µAh"
  else if ($4>0) print "    mAh"
  else print "    空"
}' "$F" | sort | uniq -c | sed 's/^/  /'
echo "  --- 问号比例 ---"
T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
echo "    总数 $T，问号 $Q ($(awk -v q=$Q -v t=$T 'BEGIN{printf "%.0f", t>0?q*100/t:0}')%)"
echo "  --- 唯一包名数 ---"
awk -F'\t' '$2!="*" && $3!="?" {print $3}' "$F" | sort -u | wc -l | sed 's/^/    /'
echo "  --- 采样跨度 ---"
awk -F'\t' '$2=="*" && $4>100000 {if(!f)f=$1; l=$1} END{
  if(f) printf "    %.2f 小时\n", (l-f)/3600000
}' "$F"

echo
echo "===== 核算 ====="
"$D/plcollect" --status -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "===== 重启 App ====="
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "  已重启"
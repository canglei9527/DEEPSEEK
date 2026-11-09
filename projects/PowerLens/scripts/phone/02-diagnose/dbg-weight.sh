#!/system/bin/sh
# dbg-weight.sh -- 定位"CPU 0m 却有占比"的原因
# 用法: su -c "sh /data/local/tmp/dbg-weight.sh"

F=/data/data/com.powerlens.app/files/snapshots.tsv

echo "########## 1. external_storage 的 uid ##########"
UID_ES=$(awk -F'\t' '$3=="external_storage"{print $2; exit}' "$F")
echo "  uid=$UID_ES"

echo
echo "########## 2. 该 uid 的所有行（前 3 / 后 3）##########"
awk -F'\t' -v u="$UID_ES" '$2==u' "$F" | head -n 3 | sed 's/^/  前 /'
echo "  ..."
awk -F'\t' -v u="$UID_ES" '$2==u' "$F" | tail -n 3 | sed 's/^/  后 /'

echo
echo "########## 3. 首末值对比（关键）##########"
awk -F'\t' -v u="$UID_ES" '$2==u' "$F" > /tmp/es.txt
FIRST=$(head -n1 /tmp/es.txt)
LAST=$(tail -n1 /tmp/es.txt)
echo "  首: $FIRST"
echo "  末: $LAST"

echo "$FIRST" | awk -F'\t' '{printf "  首: cpu=%s rx=%s tx=%s wl=%s fg=%s\n", $4,$8,$9,$6,$11}'
echo "$LAST"  | awk -F'\t' '{printf "  末: cpu=%s rx=%s tx=%s wl=%s fg=%s\n", $4,$8,$9,$6,$11}'

echo
echo "  增量:"
paste <(echo "$FIRST") <(echo "$LAST") | awk -F'\t' '{
  printf "    cpu: %d\n", $4-$14
  printf "    rx : %d\n", $8-$18
  printf "    tx : %d\n", $9-$19
}'

echo
echo "########## 4. 最后一条快照里所有 uid 的 CPU 是否都为 0 ##########"
LASTTS=$(tail -n1 "$F" | cut -f1)
echo "  最后时间戳: $LASTTS"
awk -F'\t' -v t="$LASTTS" '$1==t && $2!="*"' "$F" | awk -F'\t' '
  { n++; if ($4+0 > 0) nz++ }
  END { printf "  该快照 UID 数=%d，CPU>0 的=%d\n", n, nz }
'

echo
echo "########## 5. C 采集器直接跑一次看输出 ##########"
/data/local/tmp/plc/plcollect --once -f /data/local/tmp/dbg1.tsv 2>&1 | sed 's/^/  /'
echo "  --- CPU>0 的条目 ---"
awk -F'\t' '$2!="*" && $4>0 {printf "    uid=%-6s %-40s cpu=%s\n", $2, $3, $4}' /data/local/tmp/dbg1.tsv | head -n 10
echo "  --- external_storage 在 C 输出里 ---"
awk -F'\t' '$3=="external_storage"' /data/local/tmp/dbg1.tsv | sed 's/^/    /'

echo
echo "########## 6. 判定 ##########"
echo "  如果第 5 节里 external_storage 的 cpu=0，那 App 显示 0m 是对的，"
echo "  但占比 34.6% 说明权重不是来自 CPU —— 需要检查 Storage.wAll() 的其它项。"
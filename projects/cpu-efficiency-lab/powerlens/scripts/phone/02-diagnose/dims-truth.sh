#!/system/bin/sh
# dims-truth.sh -- 一次拿全事实：UID 头行是否重复、530965 从哪来
# 用法: su -c "sh /data/local/tmp/dims-truth.sh"

D=/data/local/tmp/bs.txt
dumpsys batterystats --charged > $D 2>/dev/null
echo "dumpsys 总行数: $(wc -l < $D)"
echo ""

echo "########## 1. UID 头行总数 vs 去重数 ##########"
grep -cE '^ +UID ' $D | sed 's/^/  总头行: /'
grep -oE '^ +UID [^ :]+' $D | sed 's/^ *//' | sort | uniq -c | sort -nr | head -n 8 | sed 's/^/  /'

echo ""
echo "########## 2. u0a341 出现的每一次（原文前 130 字符） ##########"
grep -nE '^ +UID u0a341:' $D | cut -c1-150 | sed 's/^/  /'

echo ""
echo "########## 3. charged 周期分隔标志 ##########"
echo "  --- 含 'Charging' / 'Discharge' 段落标题的行 ---"
grep -nE '^(Charger|Estimated power|Per-uid state|UID timings|.*[Ss]tates:)' $D | head -n 15 | sed 's/^/    /'
echo "  --- 顶层（无前导空格）的段落标题 ---"
grep -nE '^[A-Za-z]' $D | head -n 25 | sed 's/^/    /'

echo ""
echo "########## 4. dims.tsv 里 Δfg=530965 的那些 UID，在 dumpsys 里的真实 headline ##########"
for U in $(awk -F'\t' '$3==530965{print $2}' /data/local/tmp/powerlens/dims.tsv | head -n 4); do
  # 反推 u0a 形式
  if [ "$U" -ge 10000 ] 2>/dev/null; then
    A=$((U - 10000))
    PAT="UID u0a$A:"
  else
    PAT="UID $U:"
  fi
  echo "  --- uid=$U  找 '$PAT' ---"
  grep -E "^ +$PAT" $D | cut -c1-150 | sed 's/^/      /'
  echo "      prev 里的值: $(awk -F'\t' -v u=$U '$2==u{print "fg="$3" bg="$4" fgs="$5" cpuOff="$6}' /data/local/tmp/powerlens/dims.prev)"
done

echo ""
echo "########## 5. 全设备 fg 时长是否有聚合行（Total/合计） ##########"
grep -nE '^ +(Total|UID Total|All):' $D | head -n 5 | cut -c1-140 | sed 's/^/  /'

echo ""
echo "########## 6. 缩进 6 空格的 UID 头 vs 其他缩进 ##########"
grep -oE '^ +UID ' $D | awk '{n=0; for(i=1;i<=length($0);i++) if(substr($0,i,1)==" ") n++; print n}' | sort | uniq -c | sed 's/^/  /'

echo ""
echo "########## 7. 同一个 UID 头出现次数分布 ##########"
grep -oE '^ +UID [^ :]+' $D | sed 's/^ *//' | sort | uniq -c | awk '{print $1}' | sort | uniq -c | sed 's/^/  出现N次的UID数: /'

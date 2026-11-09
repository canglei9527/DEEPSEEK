#!/system/bin/sh
# dims-midmatch.sh -- 查 dumpsys 里所有含 "  UID " 子串的行（含行中间），
#                     对比只匹配行首的数量。多出来的就是 pldims 的误匹配源。
# 用法: su -c "sh /data/local/tmp/dims-midmatch.sh"

D=/data/local/tmp/drepro/bs.txt
if [ ! -f "$D" ]; then
  D=/data/local/tmp/bs.txt
  dumpsys batterystats --charged > $D 2>/dev/null
fi
echo "文件: $D  行数: $(wc -l < $D)"
echo ""

echo "########## 1. 行首 6 空格的正规 UID 头 ##########"
echo "  $(grep -cE '^      UID ' $D)"

echo ""
echo "########## 2. 含 '  UID ' 子串（strstr 口径）的行 ##########"
echo "  $(grep -c '  UID ' $D)"

echo ""
echo "########## 3. 差集：strstr 会误匹配的行 ##########"
grep -n '  UID ' $D | grep -v ':      UID ' | head -n 12 | cut -c1-190 | sed 's/^/  /'
echo "  误匹配总数: $(grep '  UID ' $D | grep -vc '^      UID ')"

echo ""
echo "########## 4. 这些误匹配行里，有多少带 fg:/bg:/fgs: ##########"
grep '  UID ' $D | grep -v '^      UID ' | grep -c 'fg:' | sed 's/^/  含 fg: 的行数: /'
echo "  --- 样例 ---"
grep -n '  UID ' $D | grep -v ':      UID ' | grep 'fg:' | head -n 4 | cut -c1-220 | sed 's/^/    /'

echo ""
echo "########## 5. u0a341 的 fg 被累加了多少次（模拟 pldims 逻辑） ##########"
grep '  UID ' $D | grep 'u0a341' | nl | cut -c1-190 | sed 's/^/  /'

echo ""
echo "########## 6. 全机 stats 时长（fg 不可能超过它） ##########"
grep -E '^ *duration: ' $D | head -n 3 | sed 's/^/  /'
grep -oE 'duration: [^,]*' $D | head -n 3 | sed 's/^/  /'
grep -E 'Statistics since' $D | head -n 2 | sed 's/^/  /'
grep -E '^Estimated power' $D | head -n 1 | sed 's/^/  /'

echo ""
echo "########## 7. 所有出现 fg: 且括号里是小时级的行 ##########"
grep -oE '[a-zA-Z_]*fg: [0-9.]+ \([0-9]+h[^)]*\)' $D | sort | uniq -c | sort -nr | head -n 10 | sed 's/^/  /'

echo ""
echo "########## 8. 含 fg: 的行前缀分布（缩进+首词） ##########"
grep ' fg:' $D | sed 's/^\( *\)\([^ ]*\).*/\1| \2/' | sort | uniq -c | sort -nr | head -n 10 | sed 's/^/  /'

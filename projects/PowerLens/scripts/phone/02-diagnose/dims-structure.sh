#!/system/bin/sh
# dims-structure.sh -- 打印 dumpsys batterystats 里 UID 段的真实行排布
# 用法: su -c "sh /data/local/tmp/dims-structure.sh"

D=$(mktemp /tmp/bs.XXXXXX)
dumpsys batterystats --charged > $D 2>/dev/null
echo "总行数: $(wc -l < $D)"
echo ""

echo "########## A. 第一个 off/doze 段标记的上下文（前后各 8 行） ##########"
grep -n 'screen off/doze' $D | head -n 4
echo ""
N=$(grep -n 'screen off/doze' $D | head -n 1 | cut -d: -f1)
if [ -n "$N" ]; then
  echo "  --- 行 $((N-3)) ~ $((N+8)) ---"
  sed -n "$((N-3)),$((N+8))p" $D | cat -A | sed 's/\$$//' | head -n 12 | sed 's/^/    /'
fi

echo ""
echo "########## B. UID 0 段的完整内容 ##########"
awk '/^ *UID 0:/ {p=1} p {print; c++; if(c>22) exit}' $D | sed 's/^/    /'

echo ""
echo "########## C. UID 1000 段的完整内容 ##########"
awk '/^ *UID 1000:/ {p=1} p {print; c++; if(c>25) exit}' $D | sed 's/^/    /'

echo ""
echo "########## D. 一个普通 app UID 段 ##########"
awk '/^ *UID u0a/ {p=1} p {print; c++; if(c>20) exit}' $D | sed 's/^/    /'

echo ""
echo "########## E. 所有含 cpu= 且含 off 的行 ##########"
grep -n 'cpu=' $D | grep -iE 'off|doze' | head -n 6 | sed 's/^/    /'

echo ""
echo "########## F. off/doze 出现次数 ##########"
echo "  $(grep -c 'screen off/doze' $D)"

echo ""
echo "########## G. 缩进分布（off/doze 行） ##########"
grep 'screen off/doze' $D | awk '{
  n=0
  for(i=1;i<=length($0);i++){ if(substr($0,i,1)==" ") n++; else break }
  print "  前导空格="n"  内容="substr($0,n+1,60)
}' | sort | uniq -c | sort -nr | head -n 6

rm -f $D
#!/system/bin/sh
# pldims-selftest.sh -- 对比 pldims 存的累计值 vs dumpsys 头部真实值
# 用法: su -c "sh /data/local/tmp/pldims-selftest.sh"

PREV=/data/local/tmp/powerlens/dims.prev
DIMS=/data/local/tmp/powerlens/dims.tsv

echo "########## A. dims.prev 头部（ts uid fg bg fgs cpuOff wl wlOff 累计ms） ##########"
head -n 3 $PREV | sed 's/^/  /'

echo ""
echo "########## B. dumpsys 里同一批 UID 的 headline 真实值 ##########"
dumpsys batterystats --charged 2>/dev/null | grep -E '^ +UID (u0a341|0|1000):' | sed 's/^ */  /' | cut -c1-150

echo ""
echo "########## C. prev 里 fg>bg 的 UID（对照 headline 检查是否翻倍） ##########"
awk -F'\t' '$3>0 && $3==$4 {print "  uid="$2" fg="$3" bg="$4" fgs="$5}' $PREV | head -n 5

echo ""
echo "########## D. 手动解析 uid 10341 的 headline ##########"
L=$(dumpsys batterystats --charged 2>/dev/null | grep -E '^ +UID u0a341:' | head -n 1)
echo "  原文: ${L}"
echo ""
echo "  fg 括号: $(echo "$L" | grep -oE 'fg: [0-9.]+ \([^)]*\)' | head -n 1)"
echo "  bg 括号: $(echo "$L" | grep -oE ' bg: [0-9.]+ \([^)]*\)' | head -n 1)"
echo "  fgs 括号: $(echo "$L" | grep -oE 'fgs: [0-9.]+ \([^)]*\)' | head -n 1)"

echo ""
echo "########## E. prev 里 uid 10341 存了什么 ##########"
awk -F'\t' '$2==10341 {print "  fg="$3" bg="$4" fgs="$5" cpuOff="$6" wl="$7" wlOff="$8}' $PREV

echo ""
echo "########## F. 两次采样对比（验证增量合理性） ##########"
/data/local/tmp/plc/pldims -d /tmp/st1 >/dev/null 2>&1
sleep 20
/data/local/tmp/plc/pldims -d /tmp/st1 >/dev/null 2>&1
echo "  20 秒间隔的增量（应为 ~20000ms 级别，不该超过间隔）:"
awk -F'\t' '{
  iv=$9/1000
  flag=""
  if ($3>iv+2) flag=flag" fg超!("$3/1000"s)"
  if ($4>iv+2) flag=flag" bg超!("$4/1000"s)"
  if ($5>iv+2) flag=flag" fgs超!("$5/1000"s)"
  if (flag!="") print "  uid="$2" 间隔="iv"s"flag
}' /tmp/st1/dims.tsv | head -n 10

echo ""
echo "  增量 TOP5（按 fg）:"
sort -t"$(printf '\t')" -k3 -nr /tmp/st1/dims.tsv 2>/dev/null | head -n 5 | awk -F'\t' '{print "  uid="$2" Δfg="$3/1000"s Δbg="$4/1000"s Δfgs="$5/1000"s ΔcpuOff="$6/1000"s 间隔="$9/1000"s"}'

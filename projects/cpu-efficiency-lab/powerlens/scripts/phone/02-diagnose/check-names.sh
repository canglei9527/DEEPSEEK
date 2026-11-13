#!/system/bin/sh
# check-names.sh -- 验证系统 UID 名称是否正确
# 用法: su -c "sh /data/local/tmp/check-names.sh"

F=${1:-/data/local/tmp/t2.tsv}
[ -f "$F" ] || { echo "文件不存在: $F"; exit 1; }

echo "===== 关键系统 UID 的名称 ====="
for uid in 0 1000 1001 1002 1013 1017 1036 1047 1068 1073 9999; do
  line=$(awk -F'\t' -v u="$uid" '$2==u{print $3; exit}' "$F")
  if [ -n "$line" ]; then
    printf '  uid %-6s -> %s\n' "$uid" "$line"
  else
    printf '  uid %-6s -> (不在快照中)\n' "$uid"
  fi
done

echo
echo "===== 应用 UID 的名称（应该是 com.xxx 形式）====="
awk -F'\t' '$2!="*" && $2>=10000 && $3!="?" {printf "  uid %-7s %s\n", $2, $3}' "$F" | head -n 12

echo
echo "===== 统计 ====="
T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
S=$(awk -F'\t' '$2!="*" && $2<10000' "$F" | wc -l)
P=$(awk -F'\t' '$2!="*" && $3 ~ /^\//' "$F" | wc -l)
echo "  UID 总数: $T"
echo "  pkg=?  : $Q"
echo "  系统UID(<10000): $S"
echo "  路径形式(pkg 以 / 开头，应被替换成 (uid N)): $P"

echo
echo "===== 异常检查 ====="
if [ "$P" -gt 0 ]; then
  echo "  [FAIL] 还有 $P 个 pkg 是路径形式："
  awk -F'\t' '$2!="*" && $3 ~ /^\// {printf "    uid %-7s %s\n", $2, $3}' "$F" | head -n 5
else
  echo "  [OK] 没有路径形式的 pkg"
fi

echo
echo "  uid 1000 应该是「Android 系统」:"
U1K=$(awk -F'\t' '$2==1000{print $3; exit}' "$F")
if [ "$U1K" = "Android 系统" ]; then
  echo "    [OK] $U1K"
else
  echo "    [FAIL] 实际是 '$U1K'"
fi

echo "  uid 0 应该是「root/内核」:"
U0=$(awk -F'\t' '$2==0{print $3; exit}' "$F")
if [ "$U0" = "root/内核" ]; then
  echo "    [OK] $U0"
else
  echo "    [FAIL] 实际是 '$U0'"
fi
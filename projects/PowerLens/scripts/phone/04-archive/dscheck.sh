#!/system/bin/sh
# dscheck.sh -- 检查 dumpsys batterystats --charged 的输出，对照 PowerLens 原生解析器的期望
# 用法: su -c "sh /data/local/tmp/dscheck.sh"

OUT=/data/local/tmp/ds.txt
dumpsys batterystats --charged > "$OUT" 2>&1

echo "== 基本信息 =="
echo "  bytes=$(wc -c < $OUT)"
echo "  lines=$(wc -l < $OUT)"
echo "  含 '  UID ' 的行数: $(grep -c '  UID ' $OUT)"

echo
echo "== '  UID ' 区块前 5 行 =="
grep -n '  UID ' "$OUT" | head -n 5

echo
echo "== 前 3 个 UID 区块的原始内容（各 12 行） =="
n=0
grep -n '  UID ' "$OUT" | head -n 3 | while IFS=: read -r ln rest; do
  n=$((n+1))
  echo "----- block $n @ line $ln : $rest"
  sed -n "${ln},$((ln+11))p" "$OUT" | sed 's/^/    /'
done

echo
echo "== 关键字段出现次数 =="
for k in 'TOTAL wake' 'Wi-Fi network' 'Mobile network' 'on battery, screen off/doze' 'on battery, screen on' 'Apk ' 'actual background partial' 'cpu=' 'fgs:'; do
  printf '  %-34s %s\n' "$k" "$(grep -c "$k" $OUT)"
done

echo
echo "== 如果没有 --charged =="
OUT2=/data/local/tmp/ds_nocharged.txt
dumpsys batterystats > "$OUT2" 2>&1
echo "  bytes=$(wc -c < $OUT2)  含UID=$(grep -c '  UID ' $OUT2)"

echo
echo "== reset 后的 dumpsys 大小对比 =="
ls -l "$OUT" "$OUT2" | sed 's/^/  /'
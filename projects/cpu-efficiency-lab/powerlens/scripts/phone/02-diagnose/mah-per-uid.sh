#!/system/bin/sh
# mah-per-uid.sh -- 验证 dumpsys 每 UID 的 mAh 分项是否可加总回 Global
# 用法: su -c "sh /data/local/tmp/mah-per-uid.sh"

D=/data/local/tmp/bs2.txt
[ -f "$D" ] || dumpsys batterystats > $D 2>/dev/null

echo "########## 1. Global 分项 mAh ##########"
awk '/Estimated power use/,/^\(not on battery/' $D | grep -E '^ +[a-z_]+: [0-9]' | head -n 12 | sed 's/^/  /'

echo ""
echo "########## 2. 每 UID 明细首行（全时段合计）的 mAh 字段 ##########"
echo "  --- 格式: <mAh 数值>  <UID 头> ---"
awk '
/^ +UID / { u = $0; sub(/^ +/, "", u); sub(/:.*/, "", u); have = 1; first = 1; next }
have && first && /cpu=/ && /=/ {
  # 这一行是 UID 段的"全时段合计"明细
  line = $0
  gsub(/ *\([^)]*\)/, " ", line)   # 去掉所有 (时长) 括号，只留 key=value
  print "  " line
  first = 0
  n++
  if (n >= 8) exit
}' $D | cut -c1-190

echo ""
echo "########## 3. 各 mAh 字段跨 UID 求和 vs Global ##########"
awk '
/^ +UID / { u = $0; have = 1; first = 1; next }
have && first && /cpu=/ {
  line = $0
  gsub(/ *\([^)]*\)/, " ", line)
  for (i = 1; i <= 20; i++) {
    if (match(line, /(screen|cpu|camera|audio|video|mobile_radio|sensors|gnss|wifi|wakelock|display|bluetooth|flashlight|memory|volume|ambient_display|idle|modem)=([0-9][0-9.]*)/, m)) {
      # toybox awk 不支持 match 三参数，退回手工
      break
    }
  }
  # 手工切分
  nl = split(line, tok, " ")
  for (i = 1; i <= nl; i++) {
    t = tok[i]
    if (t !~ /=/) continue
    eq = index(t, "=")
    k = substr(t, 1, eq - 1)
    v = substr(t, eq + 1) + 0
    # 只取不带冒号前缀的总项（排除 cpu:fg 之类）
    if (k ~ /:/) continue
    if (v > 0) { sum[k] += v; cnt[k]++ }
  }
  first = 0
  n++
}
END {
  printf "  %-16s %12s %8s\n", "字段", "Σ mAh", "UID数"
  for (k in sum) printf "  %-16s %12.1f %8d\n", k, sum[k], cnt[k]
}' $D

echo ""
echo "########## 4. 期望值（Global） ##########"
echo "  cpu=2359  screen=560  mobile_radio=1005  audio=41  video=51.1"
echo "  sensors=19.2  wifi=10.1  wakelock=15.9  camera=2.40  gnss=0.362"
echo ""
echo "  若 Σ(某字段) ≈ Global(该字段)，说明 per-UID mAh 可直接用于归因。"

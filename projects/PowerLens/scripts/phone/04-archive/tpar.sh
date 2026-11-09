#!/system/bin/sh
# tpar.sh -- 验证 plcollect --status 的解析（用最小样本）
BIN=/data/local/tmp/plc/plcollect
F=/data/local/tmp/plc/t.tsv

# 2400000 µAh 下降 = 2400 mAh
{
  printf '1000\t*\tbattery\t5410000\t95\tDischarging\t0\t0\t0\t0\t0\t0\n'
  printf '2000\t*\tbattery\t5400000\t95\tDischarging\t0\t0\t0\t0\t0\t0\n'
  printf '3000\t*\tbattery\t5390000\t95\tDischarging\t0\t0\t0\t0\t0\t0\n'
  # 充电跳变：应被识别为充入，不算放电
  printf '4000\t*\tbattery\t5890000\t98\tCharging\t0\t0\t0\t0\t0\t0\n'
  # 放电继续
  printf '5000\t*\tbattery\t5880000\t98\tDischarging\t0\t0\t0\t0\t0\t0\n'
} > "$F"

echo "== 样本内容 =="
cat "$F"

echo
echo "== 期望：真实放电 = 10+10+10 = 30 mAh，充入 = 500 mAh =="
echo "== 实际 =="
$BIN --status -f "$F"

echo
echo "== 再测真实 snapshots.tsv =="
$BIN --status -f /data/data/com.powerlens.app/files/snapshots.tsv

echo
echo "== 检查该文件里 battery 行数 =="
grep -c 'battery' /data/data/com.powerlens.app/files/snapshots.tsv
echo "--- 前 2 条原始 ---"
grep -m2 'battery' /data/data/com.powerlens.app/files/snapshots.tsv | od -c | head -n 8
#!/system/bin/sh
# deploy-v4.sh —— 一次性把 pldims v3 + APK 部署到位并自证正确
#
# 为什么写成脚本而不是一串 adb 命令：
#   KernelSU 的 su -c 只有第一条命令是 root，后面掉回 shell。
#   所以整段必须在 `su -c 'sh deploy-v4.sh'` 里跑，脚本内部不再依赖
#   交互式 root，所有需要特权写入的地方用 tee。
#
# 用法（PC 侧）：
#   adb push deploy-v4.sh /data/local/tmp/
#   adb shell su -c 'sh /data/local/tmp/deploy-v4.sh'

set -e
PLD=/data/local/tmp/plc
DATA=/data/local/tmp/powerlens
BS=/data/local/tmp/bs-v4.txt

echo "=== 1. 存一份现场 dumpsys 作为回归基线（-i 输入）==="
dumpsys batterystats --charged > "$BS" 2>/dev/null
echo "  $(wc -l < $BS) 行, $(wc -c < $BS) 字节"

echo
echo "=== 2. 离线解析同一份输入（不产生任何副作用）==="
$PLD/pldims -i "$BS" -d /data/local/tmp/drepro-v4 -v 2>&1 | grep -E 'GLOBAL|headline=|UID=' | head -20

echo
echo "=== 3. 与 dumpsys 原文交叉核对（唯一可信的判据）==="
# 用同一份 bs 里的原文数值 vs pldims 解析值
grep -m1 'cpu: ' "$BS" | head -1
echo "  ↑ dumpsys Global cpu；应与上面 GLOBAL cpu 一致"

echo
echo "=== 4. 用 -i 做两次「同输入」解析，结果必须逐字节相同 ==="
# 这条是回归防线：解析逻辑若有不确定性（依赖行序/状态残留），
# 同一份输入两次跑就会不同。之前 parse_dur 越界就是这类"看不出来"的错。
$PLD/pldims -i "$BS" -d /data/local/tmp/rt1 > /dev/null 2>&1
$PLD/pldims -i "$BS" -d /data/local/tmp/rt2 > /dev/null 2>&1
if [ -d /data/local/tmp/rt1 ] && [ -d /data/local/tmp/rt2 ]; then
  if diff -q /data/local/tmp/rt1/dims.prev /data/local/tmp/rt2/dims.prev > /dev/null 2>&1; then
    echo "  ✓ 同输入两次解析结果一致（确定性通过）"
  else
    echo "  ✗ 两次结果不同：解析有状态残留，必须修"
  fi
fi

echo
echo "=== 5. 重置生产基线为 v3 格式（旧 8 列 prev 会让 mAh 首轮为 0）==="
rm -f "$DATA/dims.prev"
$PLD/pldims
echo "  基线已存，列数：$(head -1 $DATA/dims.prev | awk '{print NF}')"

echo
echo "=== 6. 校验 prev 列数 = 20（8 时长 + 12 mAh）==="
N=$(head -1 "$DATA/dims.prev" | awk -F'\t' '{print NF}')
if [ "$N" = "20" ]; then
  echo "  ✓ 20 列"
else
  echo "  ✗ 得到 $N 列 —— skip_cols 类错位又出现了"
fi

echo
echo "=== 7. 物理上界自检（跑一轮增量后检查）==="
echo "  等 65 秒让守护自然跑一轮，或手动执行：sleep 65; $PLD/pldims"
echo "  然后 head -3 $DATA/dims.tsv  —— 任何单字段 µAh 都不该超过"
echo "  dims.global 里同字段的两轮差值。"

echo
echo "=== 8. 权限（App 无 root 只读，必须 666 / dirs 777）==="
chmod 777 "$DATA" 2>/dev/null || true
chmod 666 "$DATA"/* 2>/dev/null || true
ls -l "$DATA" | head -8

echo
echo "部署完成。APK 用 pm install -r 安装（切勿 uninstall，会丢 KernelSU 授权）。"

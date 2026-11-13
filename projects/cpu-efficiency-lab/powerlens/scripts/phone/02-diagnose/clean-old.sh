#!/system/bin/sh
# clean-old.sh -- 清理旧格式/伪数据行，只保留新格式（procfs µAh）数据
#
# 背景：
#   数据文件里混着三种格式，互相污染核算：
#     1. PowerLens 1.0 原始（dumpsys，电池行 mAh 整数，状态列全 0）
#     2. PowerLens 1.1 中期（App 用 BatteryManager 写的伪数据 mAh）
#     3. PowerLens 1.2 起（C 采集器，电池行 µAh + 真实状态）
#   而且 pkg 字段体系不同（旧 dumpsys 有包名，新 procfs 靠 packages.list）。
#
#   结果是 UI 上出现 "external_storage / Shell" 这类只有旧数据才有的条目，
#   且它们的 cpu 首末值跨越数据源切换，增量毫无意义。
#
# 做法：
#   备份原文件 → 只保留「最近 N 小时」且「电池行是 µAh 格式」的数据段。
#
# 用法: su -c "sh /data/local/tmp/clean-old.sh [保留小时数，默认6]"

KEEP_H=${1:-6}
PKG=com.powerlens.app
F=/data/data/$PKG/files/snapshots.tsv
D=/data/local/tmp/plc

[ -f "$F" ] || { echo "数据文件不存在: $F"; exit 1; }

BAK="$D/snapshots-before-clean-$(date +%Y%m%d-%H%M%S).tsv"
cp -f "$F" "$BAK" && echo "已备份: $BAK"

BEFORE=$(wc -l < "$F")
CUTOFF=$(( ($(date +%s) - KEEP_H * 3600) * 1000 ))

echo "保留时间戳 >= $CUTOFF （最近 ${KEEP_H} 小时）"

# 先找出「最新的 µAh 电池行」的时间戳，作为有效数据的起点
LAST_UAH_TS=$(awk -F'\t' '$2=="*" && $4>100000 {print $1}' "$F" | tail -n1)
if [ -z "$LAST_UAH_TS" ]; then
  echo "[警告] 没有找到 µAh 格式的电池行，说明 C 采集器还没写入过"
  echo "       先执行: /data/local/tmp/plc/plcollect --once -f $F"
  exit 1
fi
echo "最新 µAh 电池行时间戳: $LAST_UAH_TS"

# 找到该批次快照的起点：从最新往前，找第一个时间戳不同的电池行
# 简化做法：取「最近 KEEP_H 小时内、且属于 µAh 时代」的所有行
# µAh 时代的起点 = 第一条 µAh 电池行的时间戳
FIRST_UAH_TS=$(awk -F'\t' '$2=="*" && $4>100000 {print $1; exit}' "$F")
echo "最早 µAh 电池行时间戳: $FIRST_UAH_TS"

# 取较保守者
START=$FIRST_UAH_TS
[ "$CUTOFF" -gt "$START" ] && START=$CUTOFF
echo "有效数据起点: $START"

# 筛选：时间戳 >= START 的行
awk -F'\t' -v s="$START" '$1+0 >= s+0' "$F" > "$F.new"

AFTER=$(wc -l < "$F.new")
echo "清洗: $BEFORE -> $AFTER 行（保留 $((AFTER)) 行）"

if [ "$AFTER" -lt 100 ]; then
  echo "[警告] 保留行数太少（$AFTER），可能 START 算错了，放弃清洗"
  rm -f "$F.new"
  exit 1
fi

mv "$F.new" "$F"

# 修正权限
APPUID=$(stat -c '%u' "/data/data/$PKG/files" 2>/dev/null)
if [ -n "$APPUID" ]; then
  chown "$APPUID:$APPUID" "$F" 2>/dev/null
  chmod 644 "$F" 2>/dev/null
  CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
  [ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null
fi

echo
echo "===== 清洗后健康度 ====="
echo "  总行数: $(wc -l < $F)"
echo "  --- 电池行格式 ---"
awk -F'\t' '$2=="*" {
  if ($4 > 100000) print "    µAh(新)"
  else if ($4 > 0) print "    mAh(旧)"
  else             print "    空"
}' "$F" | sort | uniq -c | sed 's/^/  /'
echo "  --- 采样跨度 ---"
awk -F'\t' '$2=="*" && $4>100000 {if(!f){f=$1} l=$1} END{
  if (f) printf "    %.2f 小时 (%d 个点)\n", (l-f)/3600000, NR
}' "$F"
echo "  --- pkg=? 比例 ---"
TOT=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
QM=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
echo "    总 UID 行 $TOT，其中 pkg=? 的 $QM 行"

echo
echo "===== 立即采一次（确保有新数据） ====="
"$D/plcollect" --once -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "===== 核算验证 ====="
"$D/plcollect" --status -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "===== 重启 App ====="
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "  已重启"
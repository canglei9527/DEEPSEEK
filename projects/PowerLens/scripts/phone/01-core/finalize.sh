#!/system/bin/sh
# finalize.sh -- 最终定稿：应用新采集器 + 重建干净数据集 + 验证
# 用法: su -c "sh /data/local/tmp/finalize.sh"

PKG=com.powerlens.app
D=/data/local/tmp/plc
F=/data/data/$PKG/files/snapshots.tsv

echo "########## 1. 确认采集器版本 ##########"
MD5=$(md5sum "$D/plcollect" 2>/dev/null | cut -d' ' -f1)
SZ=$(wc -c < "$D/plcollect" 2>/dev/null)
echo "  plcollect: $SZ 字节  md5=$MD5"
if [ "$SZ" -lt 13000 ]; then
  echo "  [警告] 版本偏旧（新版应为 ~14128 字节）"
fi
echo -n "  含 sys_uid_name: "
strings "$D/plcollect" 2>/dev/null | grep -c 'root/内核'

echo
echo "########## 2. 杀旧守护 ##########"
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed $P"
done
rm -f "$D/daemon.pid"
sleep 2

echo
echo "########## 3. 归档旧数据，重建新数据集 ##########"
if [ -f "$F" ]; then
  BAK="$D/snapshots-archive-$(date +%Y%m%d-%H%M%S).tsv"
  cp -f "$F" "$BAK" && echo "  已归档: $BAK"
  echo "  原行数: $(wc -l < $F)"
  : > "$F"
  echo "  已清空，准备重建"
fi

echo
echo "########## 4. 连续采 3 次建立基线 ##########"
i=1
while [ $i -le 3 ]; do
  "$D/plcollect" --once -f "$F" 2>&1
  [ $i -lt 3 ] && sleep 20
  i=$((i+1))
done

echo
echo "########## 5. 修正权限 ##########"
APPUID=$(stat -c '%u' "/data/data/$PKG/files" 2>/dev/null)
echo "  App uid: $APPUID"
if [ -n "$APPUID" ]; then
  chown "$APPUID:$APPUID" "$F" 2>/dev/null
  chmod 644 "$F" 2>/dev/null
  CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
  [ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null
  ls -lZ "$F" | sed 's/^/  /'
  su "$APPUID" -c "head -c 10 '$F' >/dev/null 2>&1" && echo "  [OK] App 可读"
fi

echo
echo "########## 6. 启动守护 ##########"
setsid sh "$D/plc-daemon.sh" 600 >/dev/null 2>&1 &
sleep 3
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/  /'

echo
echo "########## 7. 数据验证 ##########"
echo "  行数: $(wc -l < $F)"
echo "  --- 电池行 ---"
awk -F'\t' '$2=="*"{printf "    ts=%s charge=%s cap=%s status=%s\n", substr($1,6), $4, $5, $6}' "$F"
echo "  --- UID 名称抽样 ---"
awk -F'\t' '$2!="*" {printf "    %-7s %s\n", $2, $3}' "$F" | head -n 10
echo "  --- 统计 ---"
T=$(awk -F'\t' '$2!="*"' "$F" | wc -l)
Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$F" | wc -l)
echo "    UID 行 $T，pkg=? $Q"
echo "    系统 UID (<10000): $(awk -F'\t' '$2!="*" && $2<10000' "$F" | wc -l)"

echo
echo "########## 8. 核算 ##########"
"$D/plcollect" --status -f "$F" 2>&1 | sed 's/^/  /'

echo
echo "########## 9. 重启 App ##########"
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "  完成"
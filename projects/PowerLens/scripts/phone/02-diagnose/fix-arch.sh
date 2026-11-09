#!/system/bin/sh
# fix-arch.sh -- 清理错误架构：杀旧采集器，只留 root C 守护
#
# 背景：
#   历史遗留三个采集器同时写同一个文件，互相覆盖 + 写伪数据：
#     1) /data/local/tmp/powerlens/daemon.sh   ← 旧版，用 dumpsys，写 mAh 当 µAh（伪数据源）
#     2) plc-daemon.sh                          ← 新版，用 C+procfs，格式正确
#     3) App 内 CollectJobService               ← 已禁用
#
#   正确架构：只有 root 侧 C 采集器写，App 只读。App 不需要 root。
#
# 用法: su -c "sh /data/local/tmp/fix-arch.sh"

D=/data/local/tmp/plc
F=/data/data/com.powerlens.app/files/snapshots.tsv
APP=com.powerlens.app

echo "########## 1. 杀掉所有旧采集器 ##########"
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep -E 'powerlens/daemon\.sh|powerlens/pass\.sh' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null && echo "  killed 旧采集器 pid=$P"
done

# 禁用开机自启的旧采集器
for f in /data/adb/service.d/30-powerlens.sh /data/adb/service.d/20-freezer.sh; do
  [ -f "$f" ] && [ "${f##*.}" != "disabled" ] && mv "$f" "$f.disabled" 2>/dev/null && echo "  已禁用 $f"
done

echo
echo "########## 2. 备份并清洗数据文件里的伪行 ##########"
if [ -r "$F" ]; then
  BAK="$D/snapshots-$(date +%Y%m%d-%H%M%S).bak"
  cp -f "$F" "$BAK" 2>/dev/null && echo "  已备份到 $BAK"

  BEFORE=$(wc -l < "$F")
  # 伪数据特征：电池行第4列 >0 且 <100000（mAh 当 µAh），或第4列=0（空行）
  # 注意：1.0 版本的历史数据也是 mAh 格式，但那是真实数据，只是单位不同。
  #       这里只删「第4列=0」的空电池行和「第6列=0 且第5列=0」的行。
  awk -F'\t' -v OFS='\t' '
    {
      # 删除：电池行且 charge=0（无法核算的空行）
      if ($2 == "*" && $4 == 0) next
      print
    }
  ' "$F" > "$F.clean" && mv "$F.clean" "$F"
  AFTER=$(wc -l < "$F")
  echo "  清洗: $BEFORE -> $AFTER 行（删了 $((BEFORE-AFTER)) 条空电池行）"

  # 权限修正让 App 能读
  APPUID=$(grep " $APP " /data/system/packages.list 2>/dev/null | awk '{print $2}')
  if [ -n "$APPUID" ]; then
    chown "$APPUID:$APPUID" "$F" 2>/dev/null
    chmod 644 "$F" 2>/dev/null
    CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
    [ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null
    echo "  已设 $F 属主=$APPUID 权限=644"
  fi
else
  echo "  数据文件不存在，跳过"
fi

echo
echo "########## 3. 重启正确的守护 ##########"
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill "$P" 2>/dev/null
done
sleep 1
if [ -x "$D/plcollect" ] && [ -f "$D/plc-daemon.sh" ]; then
  setsid sh "$D/plc-daemon.sh" 600 >/dev/null 2>&1 &
  sleep 2
  N=$(ps -A -o ARGS 2>/dev/null | grep -c 'plc-daemon.sh')
  echo "  plc-daemon.sh 已启动（匹配 $N 条）"
else
  echo "  [缺] $D/plcollect 或 $D/plc-daemon.sh 不存在"
fi

echo
echo "########## 4. 立即采一次验证格式 ##########"
"$D/plcollect" --once -f "$F" 2>&1 | sed 's/^/  /'
echo "  --- 最后 2 条电池行 ---"
awk -F'\t' '$2=="*"' "$F" 2>/dev/null | tail -n 2 | sed 's/^/    /'

echo
echo "########## 5. 最终状态 ##########"
echo "  --- 还在跑的采集相关进程 ---"
ps -A -o PID,ARGS 2>/dev/null | grep -E 'plc-daemon|powerlens/daemon|collector' | grep -v grep | sed 's/^/    /'
echo "  --- App 进程 ---"
ps -A -o PID,ARGS 2>/dev/null | grep "$APP" | grep -v grep | sed 's/^/    /'
echo ""
echo "  提示：App 现在只需要「读文件」权限，不需要 root。"
echo "        如果之前在 KernelSU 里给过 root，可以撤销，不影响功能。"
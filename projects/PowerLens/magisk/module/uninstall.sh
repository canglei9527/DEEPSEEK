#!/system/bin/sh
# uninstall.sh -- 模块卸载时执行（KernelSU 与 Magisk 都支持）
#
# 目标：
#   1. 停止所有采集进程，不留孤儿
#   2. 删除二进制与 service.d 启动项，避免「模块没了但还在跑」
#   3. 【默认保留】历史数据 —— 卸载不该毁掉测量结果
#
# 若确实要连数据一起删，卸载前手动执行：
#   su -c 'rm -rf /data/local/tmp/powerlens /data/local/tmp/plc'

PL_TMP=/data/local/tmp/plc
PL_DATA=/data/local/tmp/powerlens

# ---- 1. 停守护 ----
# 兼容老版本残留的 autopilot（已删除该功能，但旧模块可能还留着进程）
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep -E 'autopilot\.sh|plc-daemon\.sh|plcollect|pldims' | grep -v grep | awk '{print $1}'); do
  kill "$P" 2>/dev/null
done
sleep 1
# 还活着的强杀
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep -E 'autopilot\.sh|plc-daemon\.sh|plcollect|pldims' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null
done

# ---- 2. 停 Wi-Fi 保活 ----
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'wifi-ping-loop' | grep -v grep | awk '{print $1}'); do
  kill -9 "$P" 2>/dev/null
done
rm -f /data/local/tmp/keep-wifi.pid 2>/dev/null

# ---- 3. 移除 service.d 旧启动项（含被我们停用的副本）----
# customize.sh 把真正的 40-plc.sh 改名为 .disabled-by-powerlens。
# 卸载时恢复原状：如果模块目录里带着原文件就放回去，保证「卸载即回到安装前」。
SD=/data/adb/service.d/40-plc.sh
SD_OFF=/data/adb/service.d/40-plc.sh.disabled-by-powerlens
if [ -f "$SD_OFF" ]; then
  # 恢复旧启动器（内容来自模块打包的原始副本，或用户原文件）
  if [ -f "$MODDIR/bin/40-plc.sh" ]; then
    cp -f "$MODDIR/bin/40-plc.sh" "$SD" 2>/dev/null
    chmod 755 "$SD" 2>/dev/null
    rm -f "$SD_OFF" 2>/dev/null
  else
    mv "$SD_OFF" "$SD" 2>/dev/null
  fi
fi
if [ -f "$SD" ] && ! grep -q 'plc-daemon' "$SD" 2>/dev/null; then
  mv "$SD" "$SD.removed-by-powerlens" 2>/dev/null
fi

# ---- 4. 恢复 logd ----
# 卸载时如果 logd 还挂着我们的桩，必须解绑并重启服务。
# 否则「装过模块」会永久改掉系统的日志服务，属于不可接受的副作用。
if [ -x "$PL_TMP/logd-control.sh" ]; then
  sh "$PL_TMP/logd-control.sh" off >/dev/null 2>&1
else
  umount /system/bin/logd 2>/dev/null || umount -l /system/bin/logd 2>/dev/null
  start logd 2>/dev/null
fi

# ---- 5. 删二进制（保留 daemon.log 与 dims-history 便于事后分析）----
rm -f "$PL_TMP/plcollect" "$PL_TMP/pldims" "$PL_TMP/plc-daemon.sh" 2>/dev/null
rm -f "$PL_TMP/collect-control.sh" "$PL_TMP/autopilot.sh" \
      "$PL_TMP/logd-control.sh" "$PL_TMP/logd_stub" 2>/dev/null
rm -f "$PL_TMP/daemon.pid" "$PL_TMP/module-installed.txt" \
      "$PL_TMP/autopilot.pid" 2>/dev/null
# 开关状态一并清掉，避免重装后沿用旧选择造成"装了却不采集"的困惑
rm -f "$PL_TMP/collect.conf" "$PL_TMP/collect.state" \
      "$PL_TMP/collect.lock" "$PL_TMP/logd.conf" 2>/dev/null

# ---- 6. 历史数据默认保留 ----
# snapshots.tsv / dims.tsv / dims-history / daemon.log 全部留着。
# 需要清理时由用户显式删除（见文件头注释）。
echo "$(date '+%F %T') module uninstalled; data kept at $PL_DATA" >> "$PL_TMP/daemon.log" 2>/dev/null

exit 0
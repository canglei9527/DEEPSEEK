#!/system/bin/sh
# customize.sh -- KernelSU / Magisk 模块安装脚本
#
# 两种安装路径共用本文件：
#   1) 模块管理器图形界面刷入（Magisk / KernelSU App）
#   2) PowerLens App 调用 `ksud module install <zip>` 全自动安装
#
# 设计要点：
#   * 不依赖网络、不依赖外部文件，所有二进制随 zip 一起分发
#   * 安装只做「解包 + 落位 + 权限」，真正的启动交给 post-fs-data.sh / service.sh
#   * 卸载走 uninstall.sh，把进程、数据目录、service.d 残留全部清掉

SKIPUNZIP=1

PL_TMP=/data/local/tmp/plc
PL_DATA=/data/local/tmp/powerlens
PL_LOG=/data/local/tmp/plc/daemon.log

# ---------- 输出 ----------
#
# ===== 不能定义同名 ui_print 再 "检测是否存在" =====
#
# 原实现：
#     ui_print() {
#       if type ui_print >/dev/null 2>&1; then
#         ui_print "$1"      # ← 调用的就是自己
#       else
#         echo "$1"
#       fi
#     }
#
# 函数一旦定义，`type ui_print` 就永远为真，于是进入无限递归，
# 最终栈溢出段错误。真机实测（KernelSU 4.1.2 / Android 16）：
#     selfchecker: checker "remold.sh" detects sig11
#     Segmentation fault
# 表现为安装中止 `Error: Failed to install module script`，
# 且连第一行 "- PowerLens 采集器模块" 都打不出来。
#
# 修法：在定义自己的输出函数【之前】探测框架提供的 ui_print，
# 用不同名字（pl_print）避免自递归。
#
PL_UI=0
if command -v ui_print >/dev/null 2>&1; then
  PL_UI=1
fi

pl_print() {
  if [ "$PL_UI" = "1" ]; then
    ui_print "$1"
  else
    echo "$1"
  fi
}

pl_print "- PowerLens 采集器模块"
pl_print "- 安装目录: $MODPATH"

# ---------- 1. 解包 ----------
pl_print "- 解包模块文件"
unzip -o "$ZIPFILE" -x 'META-INF/*' -d "$MODPATH" >/dev/null 2>&1
if [ $? -ne 0 ]; then
  pl_print "! 解包失败"
  abort "! 请确认 zip 完整"
fi

# ---------- 2. 部署二进制到运行目录 ----------
pl_print "- 部署采集器到 $PL_TMP"
mkdir -p "$PL_TMP" "$PL_DATA"
chmod 777 "$PL_DATA" 2>/dev/null

# 备份已有版本（若存在），方便回滚
if [ -f "$PL_TMP/plcollect" ] || [ -f "$PL_TMP/pldims" ]; then
  BAK="$PL_TMP/backup-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BAK"
  for f in plcollect pldims plc-daemon.sh 40-plc.sh \
           collect-control.sh logd-control.sh logd_stub; do
    [ -f "$PL_TMP/$f" ] && cp -p "$PL_TMP/$f" "$BAK/$f" 2>/dev/null
  done
  pl_print "- 已备份旧版本到 $BAK"
fi

# logd_stub 是 ELF 桩，不是脚本，单独处理（见下）
for f in plcollect pldims plc-daemon.sh \
         collect-control.sh logd-control.sh; do
  if [ -f "$MODPATH/bin/$f" ]; then
    cp -f "$MODPATH/bin/$f" "$PL_TMP/$f"
    chmod 755 "$PL_TMP/$f"
  else
    pl_print "! 缺少 bin/$f"
  fi
done

# ---- logd 桩：只落位，不激活 ----
# 是否真的禁用 logd 由用户在 App 里选；这里只把桩准备好。
# 桩必须带 logd_exec 上下文才能被 init exec，否则 SELinux 直接拒绝，
# 表现是 logd 起不来又不断重试。
if [ -f "$MODPATH/bin/logd_stub" ]; then
  cp -f "$MODPATH/bin/logd_stub" "$PL_TMP/logd_stub"
  chmod 755 "$PL_TMP/logd_stub"
  chcon u:object_r:logd_exec:s0 "$PL_TMP/logd_stub" 2>/dev/null
  pl_print "- logd 桩已就绪（默认未启用）"
fi

# ---- 采集开关初始化 ----
# 首次安装默认 on；升级时保留用户已有选择，不能被覆盖。
if [ ! -f "$PL_TMP/collect.conf" ]; then
  echo "on" > "$PL_TMP/collect.conf"
  chmod 666 "$PL_TMP/collect.conf" 2>/dev/null
  pl_print "- 采集开关初始化为 on"
else
  pl_print "- 保留已有采集开关: $(cat "$PL_TMP/collect.conf" 2>/dev/null)"
fi
chmod 666 "$PL_TMP/collect.state" 2>/dev/null

# 数据文件：存在就不动，避免清掉历史
touch "$PL_DATA/snapshots.tsv" "$PL_DATA/dims.tsv" 2>/dev/null
chmod 666 "$PL_DATA/snapshots.tsv" "$PL_DATA/dims.tsv" 2>/dev/null

mkdir -p "$PL_TMP/dims-history"
chmod 700 "$PL_TMP/dims-history" 2>/dev/null

# ---------- 3. 停掉正在跑的旧守护 ----------
pl_print "- 停止旧守护进程"
for P in $(ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | awk '{print $1}'); do
  kill "$P" 2>/dev/null
done
rm -f "$PL_TMP/daemon.pid" 2>/dev/null

# ---------- 4. 接管开机自启（模块成为唯一入口）----------
# 背景：旧部署靠 /data/adb/service.d/40-plc.sh 开机拉起。
# 如果同时保留它，会出现两个启动器抢同一个守护：
#   模块 service.sh 先起 → 40-plc.sh 睡 30 秒后杀掉重建，白重启一次；
#   更糟的是「模块被停用」时 40-plc.sh 照样跑，停用语义失效。
#
# 所以这里直接把 40-plc.sh 改名停用（不删，内容完整保留），
# 让模块 service.sh 成为唯一启动入口。
# 卸载时 uninstall.sh 会把它改回来。
SD=/data/adb/service.d/40-plc.sh
SD_OFF=/data/adb/service.d/40-plc.sh.disabled-by-powerlens
if [ -f "$SD" ]; then
  if [ -f "$SD_OFF" ]; then
    # 已存在停用副本，直接移除当前生效的
    rm -f "$SD"
  else
    mv "$SD" "$SD_OFF" 2>/dev/null
  fi
  pl_print "- 已停用旧启动项 40-plc.sh（保留为 .disabled-by-powerlens）"
fi

# ---------- 5. 记录安装信息 ----------
cat > "$PL_TMP/module-installed.txt" <<EOF
installed_at=$(date '+%F %T')
module_version=$(grep '^version=' "$MODPATH/module.prop" | cut -d= -f2)
module_path=$MODPATH
EOF

pl_print "- 安装完成"
pl_print "- 首次生效需重启，或执行:"
pl_print "    su -c 'sh $PL_TMP/plc-daemon.sh 60 1200 &'"
pl_print "- 卸载模块会自动停止采集并保留历史数据"
#!/system/bin/sh
# post-fs-data.sh -- 模块开机早期钩子
#
# 只做「确保目录和权限正确」，不在这里拉起守护：
# post-fs-data 阶段 /data 可能还没完全就绪，起守护容易失败。
# 真正启动放在 service.sh（late_start service，时机更合适）。

MODDIR=${0%/*}
PL_TMP=/data/local/tmp/plc
PL_DATA=/data/local/tmp/powerlens

# 确保采集器可执行（有些解压方式会丢权限）
for f in plcollect pldims; do
  [ -f "$PL_TMP/$f" ] && chmod 755 "$PL_TMP/$f" 2>/dev/null
done
[ -f "$PL_TMP/plc-daemon.sh" ] && chmod 755 "$PL_TMP/plc-daemon.sh" 2>/dev/null

# 数据目录：App 需要读，所以给 777/666
mkdir -p "$PL_DATA" "$PL_TMP/dims-history" 2>/dev/null
chmod 777 "$PL_DATA" 2>/dev/null
chmod 700 "$PL_TMP/dims-history" 2>/dev/null
for f in snapshots.tsv dims.tsv; do
  [ -f "$PL_DATA/$f" ] || touch "$PL_DATA/$f" 2>/dev/null
  chmod 666 "$PL_DATA/$f" 2>/dev/null
done
#!/system/bin/sh
# fix-all2.sh -- 用绝对路径修权限（避免 su -c 环境 PATH 缺失）
# 用法: su -c "sh /data/local/tmp/fix-all2.sh"

export PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/ksu/bin:$PATH

PKG=com.powerlens.app
DIR=/data/data/$PKG/files
F=$DIR/snapshots.tsv
D=/data/local/tmp/plc

echo "########## 0. 环境检查 ##########"
echo "  PATH=$PATH"
echo "  id: $(/system/bin/id 2>&1)"
echo "  ls: $(/system/bin/ls --version 2>&1 | head -n1)"

echo
echo "########## 1. 现状（绝对路径）##########"
/system/bin/ls -ldZ "$DIR" 2>&1 | sed 's/^/    /'
/system/bin/ls -lZ "$F" 2>&1 | sed 's/^/    /'

echo
echo "########## 2. App uid ##########"
APPUID=$(/system/bin/stat -c '%u' "$DIR" 2>/dev/null)
echo "    $APPUID"

echo
echo "########## 3. root 写入测试 ##########"
if /system/bin/echo "" >> "$F" 2>/dev/null; then
  echo "  [OK] root 可写"
else
  ERR=$(/system/bin/echo "" >> "$F" 2>&1)
  echo "  [FAIL] $ERR"
fi

echo
echo "########## 4. App 读取测试 ##########"
if [ -n "$APPUID" ]; then
  if /system/bin/su "$APPUID" -c "/system/bin/head -c 10 '$F' >/dev/null 2>&1"; then
    echo "  [OK] App($APPUID) 可读"
  else
    echo "  [FAIL] App 读不到"
  fi
else
  echo "  [跳过] 无法确定 App uid"
fi

echo
echo "########## 5. 采集器实测 ##########"
"$D/plcollect" --once -f "$F" 2>&1 | sed 's/^/    /'

echo
echo "########## 6. 备选：写到 /data/local/tmp ##########"
ALT=$D/snapshots.tsv
echo "  --- 测试备选路径 ---"
"$D/plcollect" --once -f "$ALT" 2>&1 | sed 's/^/    /'
/system/bin/chmod 644 "$ALT" 2>/dev/null
/system/bin/ls -lZ "$ALT" 2>&1 | sed 's/^/    /'
echo "  备选路径行数: $(/system/bin/wc -l < $ALT 2>/dev/null)"

echo
echo "########## 7. 结论 ##########"
MAIN_OK=$("$D/plcollect" --once -f "$F" 2>&1 | /system/bin/grep -c 'UID')
if [ "$MAIN_OK" -gt 0 ]; then
  echo "  主路径可用: $F"
else
  echo "  主路径不可用，应使用备选: $ALT"
  echo "  让守护也写备选路径："
  echo "    sed -i 's#/data/data/com.powerlens.app/files/snapshots.tsv#$ALT#' $D/plc-daemon.sh"
fi
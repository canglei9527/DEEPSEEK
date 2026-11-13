#!/system/bin/sh
# fix-perm.sh -- 修正 PowerLens 数据文件权限（App 必须可读）
#
# 背景：
#   C 采集器以 root 运行，写出的文件属主是 root:root。
#   如果不改属主，App（u0_a4xx）读不到 → 界面显示"没有数据"。
#   SELinux 标签也必须跟目录一致，否则即便 chmod 644 也读不到。
#
# 用法: su -c "sh /data/local/tmp/fix-perm.sh"

PKG=com.powerlens.app
DIR=/data/data/$PKG/files
F=$DIR/snapshots.tsv

echo "########## 1. 取 App uid ##########"
# 方法1: packages.list（最可靠）
APPUID=$(grep " $PKG " /data/system/packages.list 2>/dev/null | awk '{print $2}')
echo "  packages.list -> '$APPUID'"

# 方法2: stat 目录属主
if [ -z "$APPUID" ]; then
  APPUID=$(stat -c '%u' "$DIR" 2>/dev/null)
  echo "  stat $DIR -> '$APPUID'"
fi

# 方法3: dumpsys
if [ -z "$APPUID" ]; then
  APPUID=$(dumpsys package $PKG 2>/dev/null | grep -o 'userId=[0-9]*' | head -n1 | cut -d= -f2)
  echo "  dumpsys -> '$APPUID'"
fi

if [ -z "$APPUID" ]; then
  echo "  [FAIL] 无法确定 App uid，放弃修正"
  exit 1
fi
echo "  最终 uid: $APPUID"

echo
echo "########## 2. 修正前 ##########"
ls -lZ "$DIR" 2>/dev/null | head -n 5 | sed 's/^/  /'
[ -f "$F" ] && ls -lZ "$F" | sed 's/^/  /'

echo
echo "########## 3. 取目录的 SELinux 标签 ##########"
DIRCTX=$(ls -Zd "$DIR" 2>/dev/null | awk '{print $1}')
echo "  目录标签: $DIRCTX"

echo
echo "########## 4. 修正权限 ##########"
if [ -f "$F" ]; then
  chown "$APPUID:$APPUID" "$F" 2>&1 && echo "  chown OK"
  chmod 644 "$F" 2>&1 && echo "  chmod 644 OK"
  if [ -n "$DIRCTX" ]; then
    chcon "$DIRCTX" "$F" 2>&1 && echo "  chcon OK"
  fi
else
  echo "  $F 不存在"
fi

# 顺带修 status.txt
[ -f "$DIR/status.txt" ] && {
  chown "$APPUID:$APPUID" "$DIR/status.txt" 2>/dev/null
  chmod 600 "$DIR/status.txt" 2>/dev/null
  [ -n "$DIRCTX" ] && chcon "$DIRCTX" "$DIR/status.txt" 2>/dev/null
  echo "  status.txt 已修正"
}

echo
echo "########## 5. 修正后验证 ##########"
ls -lZ "$F" 2>/dev/null | sed 's/^/  /'

echo
echo "########## 6. 用 App uid 身份实测可读性 ##########"
if su "$APPUID" -c "head -c 100 '$F' >/dev/null 2>&1"; then
  echo "  [OK] App uid 可以读取数据文件"
else
  echo "  [FAIL] App uid 仍不能读 —— SELinux 标签可能不匹配"
  echo "  当前文件标签: $(ls -Z "$F" 2>/dev/null | awk '{print $1}')"
  echo "  目录标签    : $DIRCTX"
fi

echo
echo "########## 7. 让守护进程以后自动修权限 ##########"
# 检查 plc-daemon.sh 里有没有 chown 逻辑
if grep -q 'chown' /data/local/tmp/plc/plc-daemon.sh 2>/dev/null; then
  echo "  [OK] plc-daemon.sh 已包含 chown 逻辑"
else
  echo "  [注意] plc-daemon.sh 没有 chown，每次写文件后权限会变回 root"
  echo "         建议在守护循环里加权限修正（见 ARCH-FIX.md）"
fi

echo
echo "########## 8. 触发 App 刷新 ##########"
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
sleep 4
echo "  --- logcat ---"
logcat -d -s PowerLens:* 2>/dev/null | tail -n 8 | sed 's/^/  /'
#!/system/bin/sh
# fix-all.sh -- 一次修好所有权限问题（属主 + 模式 + SELinux）
# 用法: su -c "sh /data/local/tmp/fix-all.sh"

PKG=com.powerlens.app
DIR=/data/data/$PKG/files
F=$DIR/snapshots.tsv
D=/data/local/tmp/plc

echo "########## 1. 现状 ##########"
echo "  --- 目录 ---"
ls -ldZ "$DIR" 2>/dev/null | sed 's/^/    /'
echo "  --- 文件 ---"
ls -lZ "$F" 2>/dev/null | sed 's/^/    /'
echo "  --- App uid ---"
APPUID=$(stat -c '%u' "$DIR" 2>/dev/null)
echo "    $APPUID"

echo
echo "########## 2. root 能否写入（采集器是 root）##########"
if su -c "echo test >> '$F'" 2>/dev/null; then
  echo "  [OK] root 可写"
else
  echo "  [FAIL] root 不可写 —— 这是 Permission denied 的原因"
fi

echo
echo "########## 3. 修复 ##########"
# 3.1 目录父级权限（root 需要能穿过去）
chmod 771 /data/data/$PKG 2>/dev/null
chmod 771 "$DIR" 2>/dev/null

# 3.2 文件属主设为 App，但给 root 写权限（root 本来就能写，除非 SELinux 拦）
if [ -n "$APPUID" ]; then
  chown "$APPUID:$APPUID" "$F" 2>/dev/null
fi
chmod 666 "$F" 2>/dev/null

# 3.3 SELinux：必须跟目录一致
DIRCTX=$(ls -Zd "$DIR" 2>/dev/null | awk '{print $1}')
echo "  目标标签: $DIRCTX"
if [ -n "$DIRCTX" ]; then
  chcon "$DIRCTX" "$F" 2>/dev/null
fi

echo
echo "########## 4. 修复后验证 ##########"
ls -ldZ "$DIR" 2>/dev/null | sed 's/^/    /'
ls -lZ "$F" 2>/dev/null | sed 's/^/    /'

echo
echo "  --- root 写入测试 ---"
if su -c "echo '' >> '$F'" 2>/dev/null; then
  echo "    [OK] root 可写"
else
  echo "    [FAIL] root 仍不可写"
fi

echo
echo "  --- App 读取测试 ---"
if [ -n "$APPUID" ] && su "$APPUID" -c "head -c 10 '$F' >/dev/null 2>&1"; then
  echo "    [OK] App($APPUID) 可读"
else
  echo "    [FAIL] App 读不到"
fi

echo
echo "########## 5. 采集器实测 ##########"
"$D/plcollect" --once -f "$F" 2>&1 | sed 's/^/    /'

echo
echo "########## 6. 如果失败，尝试放到 /data/local/tmp 并让 App 读 ##########"
# 备选方案：数据写在 /data/local/tmp（root 一定能写），App 通过 storeFile() 的备用路径读取
ALT=$D/snapshots.tsv
if ! su -c "echo '' >> '$F'" 2>/dev/null; then
  echo "  切到备选路径: $ALT"
  "$D/plcollect" --once -f "$ALT" 2>&1 | sed 's/^/    /'
  chmod 644 "$ALT" 2>/dev/null
  ls -lZ "$ALT" | sed 's/^/    /'
  echo "  注意：App 的 storeFile() 会比较两个位置的行数，选多的那个"
fi

echo
echo "########## 7. 重启 App ##########"
am force-stop $PKG
sleep 1
am start -n $PKG/.MainActivity >/dev/null 2>&1
echo "  完成"
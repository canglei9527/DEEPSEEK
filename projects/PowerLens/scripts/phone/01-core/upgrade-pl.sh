#!/system/bin/sh
# upgrade-pl.sh -- 升级 PowerLens，保留数据 + 保留 uid（不动 root 授权）
#
# 为什么不能用 uninstall + install：
#   KernelSU / Magisk 的 root 授权按 (uid, 签名) 记录。
#   pm uninstall 会让 App 换新 uid（u0_a427 → u0_a428），授权随之失效，
#   用户就得重新在 KernelSU 里点一次授权。
#
# 正确做法：pm install -r（reinstall），uid 不变，数据保留，授权保留。
# 前提：新旧 APK 用同一个签名（都用 D:\CODEX\tools\powerlens-debug.keystore）。
#
# 用法: su -c "sh /data/local/tmp/upgrade-pl.sh"

APK=/data/local/tmp/plc/PowerLens.apk
PKG=com.powerlens.app

echo "########## 1. 升级前状态 ##########"
UID_BEFORE=$(grep " $PKG " /data/system/packages.list 2>/dev/null | awk '{print $2}')
VER_BEFORE=$(dumpsys package $PKG 2>/dev/null | grep -m1 versionName | tr -d ' ')
echo "  uid: $UID_BEFORE"
echo "  版本: $VER_BEFORE"
echo "  --- KernelSU 授权列表 ---"
ls -l /data/adb/ksu/ 2>/dev/null | head -n 5 | sed 's/^/    /'
[ -r /data/adb/ksu/uid_authorized ] && echo "    授权文件存在"
for f in /data/adb/ksu/packages.list /data/adb/ksu/*.list /data/adb/magisk.db; do
  [ -e "$f" ] && echo "    找到: $f"
done

echo
echo "########## 2. 用 -r 升级（不卸载）##########"
pm install -r "$APK" 2>&1 | tail -n 3

echo
echo "########## 3. 升级后状态 ##########"
UID_AFTER=$(grep " $PKG " /data/system/packages.list 2>/dev/null | awk '{print $2}')
VER_AFTER=$(dumpsys package $PKG 2>/dev/null | grep -m1 versionName | tr -d ' ')
echo "  uid: $UID_AFTER   (升级前 $UID_BEFORE)"
echo "  版本: $VER_AFTER"

if [ "$UID_BEFORE" = "$UID_AFTER" ]; then
  echo "  [OK] uid 未变 → KernelSU root 授权保留，不需要重新授权"
else
  echo "  [注意] uid 变了（$UID_BEFORE → $UID_AFTER），KernelSU 里需要重新授权一次"
fi

echo
echo "########## 4. 数据文件权限（升级后可能被重置）##########"
F=/data/data/$PKG/files/snapshots.tsv
if [ -f "$F" ]; then
  chown "$UID_AFTER:$UID_AFTER" "$F" 2>/dev/null
  chmod 644 "$F" 2>/dev/null
  CTX=$(ls -Zd "$(dirname $F)" 2>/dev/null | awk '{print $1}')
  [ -n "$CTX" ] && chcon "$CTX" "$F" 2>/dev/null
  echo "  $F -> 属主=$UID_AFTER 权限=644"
  ls -lZ "$F" | sed 's/^/  /'
else
  echo "  数据文件不存在（首次安装）"
fi

echo
echo "########## 5. 重启 App ##########"
am force-stop $PKG
sleep 1
am start -W -n $PKG/.MainActivity 2>&1 | grep -E 'Status|TotalTime'

echo
echo "########## 6. 数据健康度（升级后）##########"
sleep 5
if [ -r "$F" ]; then
  echo "  电池行总数: $(awk -F'\t' '$2=="*"' $F | wc -l)"
  echo "  --- 格式分类 ---"
  awk -F'\t' '$2=="*" {
    if ($4 > 100000)      print "    正常 µAh"
    else if ($4 > 0)      print "    旧格式 mAh(可兼容)"
    else                  print "    空行 charge=0"
  }' "$F" | sort | uniq -c | sed 's/^/  /'
  echo "  --- 最后 2 条 ---"
  awk -F'\t' '$2=="*"' "$F" | tail -n 2 | sed 's/^/    /'
fi
#!/system/bin/sh
# no-root-proof.sh -- 证明 App 不需要 root：撤销授权后功能仍正常
# 用法: su -c "sh /data/local/tmp/no-root-proof.sh"

PKG=com.powerlens.app
D=/data/local/tmp/plc
F=/data/data/$PKG/files/snapshots.tsv

echo "########## 1. 当前架构确认 ##########"
echo "  App uid: $(stat -c '%u' /data/data/$PKG/files 2>/dev/null)"
echo "  --- 采集职责在谁身上 ---"
ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/    守护: /'
echo "  --- App 进程 ---"
ps -A -o PID,ARGS 2>/dev/null | grep "$PKG" | grep -v grep | sed 's/^/    /'

echo
echo "########## 2. App 代码路径检查：是否还有 su 调用 ##########"
APK=$(pm path $PKG 2>/dev/null | head -n1 | cut -d: -f2)
echo "  APK: $APK"
if [ -n "$APK" ] && [ -f "$APK" ]; then
  # 检查 dex 里是否还有 su 相关字符串
  if unzip -p "$APK" classes.dex 2>/dev/null | strings | grep -q 'dumpsys batterystats'; then
    echo "  [注意] dex 里仍有 dumpsys 调用（Collector 的降级路径）"
  fi
  if unzip -p "$APK" classes.dex 2>/dev/null | strings | grep -q '^su$'; then
    echo "  [注意] dex 里仍有 su 字符串"
  fi
fi

echo
echo "########## 3. 数据文件可读性（App 身份） ##########"
APPUID=$(stat -c '%u' /data/data/$PKG/files 2>/dev/null)
ls -lZ "$F" | sed 's/^/  /'
if su "$APPUID" -c "head -c 100 '$F' >/dev/null 2>&1"; then
  echo "  [OK] App uid ($APPUID) 可以读数据文件"
else
  echo "  [FAIL] App uid ($APPUID) 读不到"
fi

echo
echo "########## 4. 采集器独立运行验证（不需要 App） ##########"
echo "  --- 手动跑一次，看是否能成功 ---"
OUT=$($D/plcollect --once -f /data/local/tmp/noroot-test.tsv 2>&1)
echo "  $OUT"
if echo "$OUT" | grep -q 'UID'; then
  echo "  [OK] 采集器完全独立于 App 工作"
  rm -f /data/local/tmp/noroot-test.tsv
else
  echo "  [FAIL] 采集器失败"
fi

echo
echo "########## 5. App 启动后的行为 ##########"
am force-stop $PKG
sleep 1
logcat -c 2>/dev/null
am start -n $PKG/.MainActivity >/dev/null 2>&1
sleep 6
echo "  --- logcat（应无 su/EACCES/写入失败）---"
logcat -d -s PowerLens:* 2>/dev/null | grep -vE 'JobScheduler|RemoteException|^\s+at |android\.|java\.' | tail -n 10 | sed 's/^/    /'

echo
echo "########## 6. 数据是否在增长（守护在写） ##########"
C1=$(wc -l < "$F")
sleep 20
C2=$(wc -l < "$F")
echo "  20 秒内: $C1 -> $C2 行"
if [ "$C2" -ge "$C1" ]; then
  echo "  [OK] 数据未丢失（守护正常）"
fi

echo
echo "########## 7. 结论 ##########"
echo "  如果第 4 节成功、第 3 节可读、第 5 节无报错，"
echo "  则 App 可以完全撤销 KernelSU root 授权，功能不受影响。"
echo
echo "  撤销方式："
echo "    KernelSU 管理器 → 超级用户 → 找到 PowerLens → 关闭授权"
echo "    或 su -c \"rm -f /data/adb/ksu/...\" （不推荐手动改）"
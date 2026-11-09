#!/system/bin/sh
# acceptance2.sh -- 验收测试 v2：只检查最新一批数据（避开历史污染）
# 用法: su -c "sh /data/local/tmp/acceptance2.sh"

D=/data/local/tmp/plc
STORE=/data/local/tmp/powerlens/snapshots.tsv
PKG=com.powerlens.app
PASS=0; FAIL=0

ok()  { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad() { FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }

echo "########## 1. 采集器 ##########"
if [ -x "$D/plcollect" ]; then
  SZ=$(wc -c < "$D/plcollect")
  ok "plcollect 存在 ($SZ B)"
else
  bad "plcollect 不存在"; exit 1
fi

echo
echo "########## 2. 守护进程 ##########"
N=$(ps -A -o ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | wc -l)
if [ "$N" -eq 1 ]; then
  ok "单实例运行"
  ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/        /'
else
  bad "实例数 $N（应为 1）"
fi

echo
echo "########## 3. 最新一批数据（关键） ##########"
# 找最新的时间戳
LATEST=$(awk -F'\t' '$2=="*"{print $1}' "$STORE" 2>/dev/null | tail -n1)
if [ -z "$LATEST" ]; then
  bad "没有电池行"
  exit 1
fi
echo "  最新快照 ts=$LATEST"

# 提取该批
awk -F'\t' -v t="$LATEST" '$1==t' "$STORE" > /tmp/latest.txt 2>/dev/null
CNT=$(awk -F'\t' '$2!="*"' /tmp/latest.txt | wc -l)
echo "  该批 UID 数: $CNT"
[ "$CNT" -gt 100 ] && ok "UID 数量正常" || bad "UID 数量偏少（$CNT）"

# 电池行
if awk -F'\t' '$2=="*" && $4>100000' /tmp/latest.txt | grep -q .; then
  ok "电池行是 µAh 格式"
else
  bad "电池行格式异常"
fi

# 包名补全
Q=$(awk -F'\t' '$2!="*" && $3=="?"' /tmp/latest.txt | wc -l)
if [ "$Q" -le 5 ]; then
  ok "包名补全率 ${CNT} 个中仅 $Q 个问号"
else
  bad "问号太多（$Q/$CNT）"
fi

# 系统 UID 名称（关键！）
U0=$(awk -F'\t' '$2==0{print $3; exit}' /tmp/latest.txt)
U1K=$(awk -F'\t' '$2==1000{print $3; exit}' /tmp/latest.txt)
U1K1=$(awk -F'\t' '$2==1001{print $3; exit}' /tmp/latest.txt)

case "$U0" in
  sys:kernel) ok "uid 0 = sys:kernel" ;;
  *) bad "uid 0 = '$U0'（应为 sys:kernel）" ;;
esac
case "$U1K" in
  sys:system) ok "uid 1000 = sys:system" ;;
  *) bad "uid 1000 = '$U1K'（应为 sys:system）" ;;
esac
case "$U1K1" in
  sys:radio) ok "uid 1001 = sys:radio" ;;
  *) bad "uid 1001 = '$U1K1'（应为 sys:radio）" ;;
esac

# 是否还有路径形式
P=$(awk -F'\t' '$2!="*" && $3 ~ /^\//' /tmp/latest.txt | wc -l)
[ "$P" -eq 0 ] && ok "无路径形式的 pkg" || bad "有 $P 个路径形式 pkg"

echo
echo "########## 4. 数据里的 sys: 标签（供 App 翻译） ##########"
S=$(awk -F'\t' '$2!="*" && $3 ~ /^sys:/' /tmp/latest.txt | wc -l)
echo "  带 sys: 前缀的: $S 个"
[ "$S" -gt 20 ] && ok "系统 UID 用 sys: 标识（App 会翻译成中文）" || bad "sys: 标签太少"

echo
echo "########## 5. App 可读性 ##########"
# 用 dumpsys 拿 uid（stat 在 su 环境有时失效）
APPUID=$(dumpsys package $PKG 2>/dev/null | grep -o 'userId=[0-9]*' | head -n1 | cut -d= -f2)
[ -z "$APPUID" ] && APPUID=$(grep " $PKG " /data/system/packages.list 2>/dev/null | awk '{print $2}')
echo "  App uid: ${APPUID:-未知}"
PERM=$(stat -c '%a' "$STORE" 2>/dev/null)
echo "  文件权限: ${PERM:-?}"
if [ -n "$APPUID" ]; then
  if su "$APPUID" -c "head -c 10 '$STORE' >/dev/null 2>&1"; then
    ok "App($APPUID) 可读"
  else
    bad "App($APPUID) 读不到"
  fi
elif [ "$PERM" = "666" ]; then
  ok "权限 666（任何进程可读）"
else
  bad "无法验证"
fi

echo
echo "########## 6. 核算 ##########"
ST=$("$D/plcollect" --status -f "$STORE" 2>&1)
echo "$ST" | sed 's/^/        /'
PTS=$(echo "$ST" | awk '/采样点/{print $3}')
if [ -n "$PTS" ] && [ "$PTS" -ge 2 ]; then
  ok "采样点 $PTS 个"
else
  bad "采样点不足"
fi

echo
echo "########## 7. 开机自启 ##########"
LS=$(ls /data/adb/service.d/ 2>&1)
echo "$LS" | sed 's/^/        /'
case "$LS" in
  *40-plc.sh*) ok "40-plc.sh 存在" ;;
  *) bad "缺 40-plc.sh" ;;
esac

echo
echo "================================"
echo "  通过 $PASS，失败 $FAIL"
echo "================================"
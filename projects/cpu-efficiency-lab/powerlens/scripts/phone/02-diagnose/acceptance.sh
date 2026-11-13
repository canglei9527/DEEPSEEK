#!/system/bin/sh
# acceptance.sh -- 验收测试：确认所有功能正常
# 用法: su -c "sh /data/local/tmp/acceptance.sh"

D=/data/local/tmp/plc
STORE=/data/local/tmp/powerlens/snapshots.tsv
PKG=com.powerlens.app
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  [FAIL] $1"; }

echo "########## 1. 采集器 ##########"
if [ -x "$D/plcollect" ]; then
  SZ=$(wc -c < "$D/plcollect")
  ok "plcollect 存在 ($SZ B)"
  if [ "$SZ" -gt 13000 ]; then
    ok "版本较新（含 sys: 标签 + packages.list 补全）"
  else
    bad "版本偏旧（$SZ B，新版约 14128）"
  fi
else
  bad "plcollect 不存在"
fi

echo
echo "########## 2. 守护进程 ##########"
N=$(ps -A -o ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | wc -l)
if [ "$N" -eq 1 ]; then
  ok "单实例运行"
  ps -A -o PID,ARGS 2>/dev/null | grep 'plc-daemon.sh' | grep -v grep | sed 's/^/        /'
elif [ "$N" -eq 0 ]; then
  bad "守护未运行"
else
  bad "有 $N 个实例（重复）"
fi

echo
echo "########## 3. 数据文件 ##########"
if [ -f "$STORE" ]; then
  L=$(wc -l < "$STORE")
  ok "数据文件存在（$L 行）"
  if [ "$L" -gt 1000 ]; then
    ok "数据量充足"
  else
    bad "数据量偏少（$L 行）"
  fi
  # 电池行格式
  UAH=$(awk -F'\t' '$2=="*" && $4>100000' "$STORE" | wc -l)
  MAH=$(awk -F'\t' '$2=="*" && $4>0 && $4<100000' "$STORE" | wc -l)
  EMPTY=$(awk -F'\t' '$2=="*" && $4==0' "$STORE" | wc -l)
  echo "        电池行: µAh=$UAH  mAh=$MAH  空=$EMPTY"
  if [ "$UAH" -gt 0 ]; then ok "有新格式电池行"; else bad "没有 µAh 格式"; fi
  # 包名补全
  T=$(awk -F'\t' '$2!="*"' "$STORE" | wc -l)
  Q=$(awk -F'\t' '$2!="*" && $3=="?"' "$STORE" | wc -l)
  if [ "$T" -gt 0 ]; then
    PCT=$((Q * 100 / T))
    if [ "$PCT" -lt 10 ]; then
      ok "包名补全率 $((100-PCT))%（问号 $Q/$T）"
    else
      bad "问号比例偏高 $PCT%（$Q/$T）"
    fi
  fi
else
  bad "数据文件不存在: $STORE"
fi

echo
echo "########## 4. UID 名称（关键） ##########"
U0=$(awk -F'\t' '$2==0{print $3; exit}' "$STORE" 2>/dev/null)
U1K=$(awk -F'\t' '$2==1000{print $3; exit}' "$STORE" 2>/dev/null)
if [ -n "$U0" ]; then
  case "$U0" in
    sys:kernel|root*) ok "uid 0 = $U0" ;;
    *) bad "uid 0 = $U0（应为 sys:kernel）" ;;
  esac
fi
if [ -n "$U1K" ]; then
  case "$U1K" in
    sys:system|Android*) ok "uid 1000 = $U1K" ;;
    *) bad "uid 1000 = $U1K（应为 sys:system）" ;;
  esac
fi
# 检查有无乱码
BAD=$(awk -F'\t' '$2!="*" && $3 ~ /[^\x00-\x7f]/' "$STORE" 2>/dev/null | wc -l)
if [ "$BAD" -eq 0 ]; then
  ok "数据里无中文（由 App 侧翻译）"
else
  echo "        注意：有 $BAD 行含非 ASCII"
fi

echo
echo "########## 5. App 可读性 ##########"
APPUID=$(stat -c '%u' /data/data/$PKG 2>/dev/null)
if [ -n "$APPUID" ]; then
  if su "$APPUID" -c "head -c 10 '$STORE' >/dev/null 2>&1"; then
    ok "App($APPUID) 可读数据"
  else
    bad "App($APPUID) 读不到数据"
  fi
else
  bad "无法确定 App uid"
fi
PERM=$(stat -c '%a' "$STORE" 2>/dev/null)
echo "        文件权限: ${PERM:-?}"

echo
echo "########## 6. 采集能力（独立于 App） ##########"
OUT=$("$D/plcollect" --once -f /data/local/tmp/acc-test.tsv 2>&1)
if echo "$OUT" | grep -q 'UID'; then
  ok "采集器独立工作正常"
  echo "        $OUT"
  rm -f /data/local/tmp/acc-test.tsv
else
  bad "采集器失败: $OUT"
fi

echo
echo "########## 7. 核算正确性 ##########"
ST=$("$D/plcollect" --status -f "$STORE" 2>&1)
echo "$ST" | sed 's/^/        /'
PTS=$(echo "$ST" | awk '/采样点/{print $3}')
if [ -n "$PTS" ] && [ "$PTS" -ge 2 ]; then
  ok "采样点 $PTS 个（可算差值）"
else
  bad "采样点不足"
fi

echo
echo "########## 8. 开机自启 ##########"
if [ -f /data/adb/service.d/40-plc.sh ]; then
  ok "40-plc.sh 已安装"
  grep -q 'plc-daemon' /data/adb/service.d/40-plc.sh && ok "内容正确" || bad "内容不含 plc-daemon"
else
  bad "缺 40-plc.sh"
fi

echo
echo "================================"
echo "  通过 $PASS 项，失败 $FAIL 项"
echo "================================"
[ "$FAIL" -eq 0 ] && echo "  [全部通过]" || echo "  [有失败项，需处理]"
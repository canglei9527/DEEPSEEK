#!/system/bin/sh
# noactive-write.sh -- 直接重写 MasterConfig.json（最可靠，不用 sed/awk 拼 JSON）
#
# 为什么不用 sed/awk 改 JSON：
#   实测 awk -v 传引号会丢（"$pkg" 变成空字符串），产出的 JSON 是坏的。
#   直接生成完整文件更安全 —— NoActive 的配置结构很简单，只有 12 个字段。
#
# 用法:
#   su -c "sh /data/local/tmp/noactive-write.sh list"
#   su -c "sh /data/local/tmp/noactive-write.sh set com.a.b com.c.d ..."
#   su -c "sh /data/local/tmp/noactive-write.sh add com.a.b"
#   su -c "sh /data/local/tmp/noactive-write.sh add-default"
#   su -c "sh /data/local/tmp/noactive-write.sh clear"

CFG=/data/system/NoActive_fJIecxAm/config
MASTER=$CFG/MasterConfig.json
BAK=$CFG/MasterConfig.json.bak

# 危险包黑名单
DENY="android com.android.systemui com.android.phone com.android.settings com.miui.home com.android.launcher3 com.miui.securitycenter com.android.providers.telephony com.android.server.telecom com.google.android.gms com.miui.powerkeeper com.miui.securitycenter.remote com.android.nfc com.miui.home.recents"

is_deny() {
  for d in $DENY; do [ "$1" = "$d" ] && return 0; done
  return 1
}

# 从现有配置里提取 userAppSet（用 grep 抓引号里的包名）
get_current() {
  awk '
    /"userAppSet"/{f=1}
    f && /\]/{exit}
    f {
      # 抓第一个引号对里的内容
      n = split($0, a, "\"")
      if (n >= 2) {
        for (i = 1; i < n; i++) {
          if (a[i] ~ /[a-zA-Z0-9_.]/ && length(a[i]) > 3) print a[i]
        }
      }
    }
  ' "$MASTER" 2>/dev/null | grep -E '^[a-zA-Z]' | sort -u
}

write_master() {
  # $@ = 包名列表
  local bak_done=0
  if [ -f "$MASTER" ] && [ ! -f "$BAK" ]; then
    cp -f "$MASTER" "$BAK" && bak_done=1
  fi

  # 生成新文件
  {
    echo "{"
    echo '	"backgroundOptimize":false,'
    echo '	"commandInject":false,'
    echo '	"dozeType":"locked",'
    echo '	"freezeBinder":false,'
    echo '	"memoryOptimize":false,'
    echo '	"netLink":0,'
    echo '	"rotationInterval":360,'
    echo '	"rotationThaw":false,'
    echo '	"skipXposed":true,'
    echo '	"startLimit":false,'
    echo '	"systemAppSet":['
    echo '		'
    echo '	],'
    echo '	"userAppSet":['
    local first=1
    for p in "$@"; do
      if [ $first -eq 1 ]; then
        printf '\t\t"%s"\n' "$p"
        first=0
      else
        printf '\t\t,"%s"\n' "$p"
      fi
    done
    [ $first -eq 1 ] && echo '		'
    echo '	]'
    echo "}"
  } > /tmp/master_new.json

  # 校验：必须是合法 JSON 结构（用 python 或简单检查）
  local L=$(wc -l < /tmp/master_new.json)
  if [ "$L" -lt 15 ]; then
    echo "[失败] 生成的文件行数异常（$L）"
    return 1
  fi

  cp -f /tmp/master_new.json "$MASTER"
  chown system:system "$MASTER" 2>/dev/null
  chmod 600 "$MASTER" 2>/dev/null
  chcon u:object_r:system_data_file:s0 "$MASTER" 2>/dev/null
  echo "[OK] 已写入（$# 个包）"
}

ACTION=${1:-list}
shift 2>/dev/null

case "$ACTION" in
  list)
    echo "=== 当前 userAppSet ==="
    get_current
    echo
    echo "=== 文件权限 ==="
    ls -lZ "$MASTER" 2>&1
    ;;
  clear)
    write_master
    ;;
  set)
    write_master "$@"
    ;;
  add)
    PKG=$1
    [ -z "$PKG" ] && { echo "需要包名"; exit 1; }
    is_deny "$PKG" && { echo "[拒绝] $PKG 在危险黑名单"; exit 1; }
    CUR=$(get_current)
    # 去重
    ALL=$(printf '%s\n%s\n' "$CUR" "$PKG" | grep -E '^[a-zA-Z]' | sort -u)
    write_master $ALL
    ;;
  add-default)
    # 推荐名单：内存大、后台常驻、无即时通讯需求
    DEF="com.coolapk.market
com.baidu.BaiduMap
tv.danmaku.bili
com.icbc
com.novel.jingxiao
com.xingin.xhs
com.taobao.idlefish
com.jingdong.app.mall
com.xunmeng.pinduoduo
com.sankuai.meituan
com.dianping.v1
com.taobao.taobao
com.eg.android.AlipayGphone
com.miui.gallery
com.ss.android.ugc.aweme"
    CUR=$(get_current)
    ALL=$(printf '%s\n%s\n' "$CUR" "$DEF" | grep -E '^[a-zA-Z]' | sort -u)
    write_master $ALL
    ;;
  *)
    echo "用法: $0 {list|set PKG...|add PKG|add-default|clear}"
    ;;
esac

echo
echo "=== 结果 ==="
cat "$MASTER" 2>&1
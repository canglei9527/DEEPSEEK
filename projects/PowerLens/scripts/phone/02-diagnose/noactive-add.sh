#!/system/bin/sh
# noactive-add.sh -- 往 NoActive 名单里加应用（保持 system:system 600 标签）
#
# 背景：
#   NoActive 一直在正常工作（LSPosed 日志证明 hook 全部成功、
#   深度睡眠反复进出、ReKernel 临时解冻都在跑）。
#   但 MasterConfig.json 里 systemAppSet / userAppSet 都是空的，
#   所以它没有冻结对象。
#
#   本脚本往 userAppSet 加包名。这个名单是「墓碑名单」——
#   被加进去的应用在息屏/后台时会被冻结（cgroup v2 cgroup.freeze）。
#
# 用法:
#   su -c "sh /data/local/tmp/noactive-add.sh list"              查看当前
#   su -c "sh /data/local/tmp/noactive-add.sh add com.foo.bar"   添加
#   su -c "sh /data/local/tmp/noactive-add.sh remove com.foo.bar" 移除
#   su -c "sh /data/local/tmp/noactive-add.sh add-default"       加推荐名单
#
# 危险包黑名单（加了会破坏系统功能）
DENY="android com.android.systemui com.android.phone com.android.settings
com.miui.home com.android.launcher3 com.miui.securitycenter
com.android.providers.telephony com.android.server.telecom
com.google.android.gms com.miui.powerkeeper com.miui.securitycenter.remote"

CFG=/data/system/NoActive_fJIecxAm/config
MASTER=$CFG/MasterConfig.json

ACTION=${1:-list}
PKG=$2

# 检查是否危险包
is_deny() {
  for d in $DENY; do
    [ "$1" = "$d" ] && return 0
  done
  return 1
}

# 用 Python 风格的手工 JSON 编辑（设备上没有 jq/python3 保证）
# NoActive 的 JSON 是 "\t" 缩进、每项一行、带末尾逗号处理的格式
do_list() {
  echo "=== 当前名单 ==="
  su -c "cat $MASTER" 2>/dev/null
}

do_add() {
  local pkg=$1
  [ -z "$pkg" ] && { echo "需要包名"; exit 1; }
  is_deny "$pkg" && { echo "[拒绝] $pkg 在危险黑名单里"; exit 1; }

  # 检查是否已存在
  if su -c "grep -q '\"$pkg\"' $MASTER" 2>/dev/null; then
    echo "[跳过] $pkg 已在名单里"
    return 0
  fi

  # 用 sed 在 userAppSet 的 "[" 后面插入
  # 先备份
  su -c "cp -f $MASTER $CFG/MasterConfig.json.bak" 2>/dev/null

  # 读取当前内容，判断 userAppSet 是否为空
  local content
  content=$(su -c "cat $MASTER" 2>/dev/null)

  # 用 awk 精确插入：找到 "userAppSet" 后的第一个 "["，在其后插入
  su -c "awk -v p=\"\\\"\$pkg\\\"\" '
    /\"userAppSet\"/{inus=1}
    inus && /\[/ && !done {
      print \$0
      print \"\\t\\t\" p \",\"
      done=1
      next
    }
    {print}
  ' $MASTER > /tmp/mc.json" 2>/dev/null

  if [ -s /tmp/mc.json ]; then
    su -c "cp -f /tmp/mc.json $MASTER" 2>/dev/null
    su -c "chown system:system $MASTER" 2>/dev/null
    su -c "chmod 600 $MASTER" 2>/dev/null
    su -c "chcon u:object_r:system_data_file:s0 $MASTER" 2>/dev/null
    echo "[OK] 已添加 $pkg"
  else
    echo "[失败] awk 处理失败"
  fi
}

do_add_default() {
  # 推荐名单：内存占用大、后台常驻、用户体验影响小的应用
  # 依据：PowerLens 实测的 RSS+Swap 排名前列，且不是通讯类
  for p in \
    com.coolapk.market \
    com.baidu.BaiduMap \
    tv.danmaku.bili \
    com.icbc \
    com.novel.jingxiao \
    com.xingin.xhs \
    com.taobao.idlefish \
    com.jingdong.app.mall \
    com.xunmeng.pinduoduo \
    com.sankuai.meituan \
    com.dianping.v1 \
    com.taobao.taobao \
    com.eg.android.AlipayGphone \
    com.alibaba.android.rimet \
    ; do
    do_add "$p"
  done
  echo
  echo "注意：com.tencent.mm（微信）和 com.tencent.mobileqq（QQ）"
  echo "      是通讯类，冻结会导致消息延迟，默认不加。"
}

case "$ACTION" in
  list)        do_list ;;
  add)         do_add "$PKG" ;;
  add-default) do_add_default ;;
  remove)
    [ -z "$PKG" ] && { echo "需要包名"; exit 1; }
    su -c "cp -f $MASTER $CFG/MasterConfig.json.bak" 2>/dev/null
    su -c "grep -v '\"$PKG\"' $MASTER > /tmp/mc2.json" 2>/dev/null
    su -c "cp -f /tmp/mc2.json $MASTER" 2>/dev/null
    su -c "chown system:system $MASTER; chmod 600 $MASTER" 2>/dev/null
    echo "[OK] 已移除 $PKG"
    ;;
  *) echo "用法: $0 {list|add PKG|add-default|remove PKG}" ;;
esac

echo
echo "=== 验证 JSON 合法性 ==="
su -c "cat $MASTER" 2>/dev/null | head -n 40
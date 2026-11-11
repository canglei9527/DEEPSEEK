#!/system/bin/sh
# logd-control.sh -- 可选：禁用 Android 的 logd 日志服务
#
# ===== 为什么放进 PowerLens 模块 =====
#   logd 是常驻服务，即使低频也会周期性唤醒 CPU、写 eMMC。
#   在追功耗时它属于「测量噪声」：既消耗电，又把 CPU 唤醒行为搞脏。
#   原来这是独立模块 close_logd，两套模块各自 mount 同一个
#   /system/bin/logd，会互相覆盖、顺序不可控。合并成一个模块后
#   只有一个 mount 点和一份开关，语义明确。
#
# ===== 为什么不能用「空文件替换」=====
#   实测：直接把 logd 换成空文件，init 发现 exec 失败会不断重试，
#   形成重启循环（logd 反复 spawn，比不关更耗电）。
#   正确做法有一个能在 exec 后立刻 pause 的 ELF 桩：
#     init exec 它 → 它立刻 pause() → 进程存在但不干活
#     init 认为服务已起，不再重试
#
# ===== 为什么必须手动 mount --bind =====
#   本机 KernelSU 下模块自带的 system/bin/logd 并没有被自动挂载
#   （overlay 未覆盖到该路径），所以这里显式 bind mount，
#   并在完成后用 cmp 校验，失败就明确报错而不是假装成功。
#
# ===== 用法 =====
#   sh logd-control.sh on     禁用 logd（bind 桩 + stop）
#   sh logd-control.sh off    恢复（umount + start）
#   sh logd-control.sh status 查看当前状态
#
# 开关持久化在 /data/local/tmp/plc/logd.conf，重启后由 service.sh 重放。

MODDIR=${0%/*}
STUB="$MODDIR/logd_stub"
[ -f "$STUB" ] || STUB=/data/local/tmp/plc/logd_stub

DST=/system/bin/logd
LOG=/data/local/tmp/plc/daemon.log
CONF=/data/local/tmp/plc/logd.conf

say() {
  echo "$(date '+%F %T') logd: $*" >> "$LOG" 2>/dev/null
  echo "logd: $*"
}

# ===== 为什么不能只用 cmp 判断 =====
#   旧 close_logd 模块是用 overlay 覆盖 system/bin/logd 实现的，
#   覆盖后 /system/bin/logd 的内容同样等于桩 → cmp 判定为"已挂载"，
#   但其实没有 bind mount。两者对用户效果相同（logd 都是桩），
#   但对「恢复」差别很大：
#     bind    → umount 即可立刻恢复
#     overlay → 只能停用旧模块 + 重启
#   所以要分开报告，off 时才能给出正确指引。
is_bound() {
  mount 2>/dev/null | grep "on $DST " | grep -qF "$STUB"
}

is_stub_content() {
  cmp -s "$STUB" "$DST" 2>/dev/null
}

use_stub() {
  if [ ! -f "$STUB" ]; then
    say "ERROR 缺少桩文件 $STUB"
    return 1
  fi
  # 桩必须能作为 logd_exec 执行，否则 SELinux 会拒绝
  chown 0:0 "$STUB" 2>/dev/null
  chmod 0755 "$STUB" 2>/dev/null
  chcon u:object_r:logd_exec:s0 "$STUB" 2>/dev/null

  if is_bound; then
    say "已挂载（跳过重复 bind）"
  elif is_stub_content; then
    # 内容已经是桩（旧模块 overlay 造成），无需再 bind
    say "桩已生效（来自其他模块的 overlay，无需重复 bind）"
    return 0
  else
    mount --bind "$STUB" "$DST" 2>/dev/null \
      || mount -o bind "$STUB" "$DST" 2>/dev/null
  fi

  if is_stub_content; then
    return 0
  fi
  say "ERROR bind 失败：$STUB -> $DST"
  return 1
}

case "$1" in
  on)
    use_stub || { echo "off" > "$CONF"; exit 1; }
    stop logd 2>/dev/null
    sleep 2
    echo "on" > "$CONF"
    say "已禁用  state=$(getprop init.svc.logd) pid=$(pidof logd)"
    ;;
  off)
    # 先停服务再解绑，避免 init 在解绑瞬间 exec 到被替换的 inode
    stop logd 2>/dev/null
    if is_bound; then
      umount "$DST" 2>/dev/null || umount -l "$DST" 2>/dev/null
      start logd 2>/dev/null
      sleep 2
      echo "off" > "$CONF"
      say "已恢复  state=$(getprop init.svc.logd) pid=$(pidof logd)"
    elif is_stub_content; then
      # 没法在线恢复：内容被别的模块 overlay 掉了
      echo "off" > "$CONF"
      say "已记录为 off，但当前桩来自其他模块的 overlay，需重启生效"
      echo "已设为 off，但 /system/bin/logd 仍被其他模块覆盖。"
      echo "请停用旧的 close_logd 模块后重启，logd 才会恢复。"
      exit 1
    else
      # 本来就是原版，只需确保服务在跑
      start logd 2>/dev/null
      sleep 2
      echo "off" > "$CONF"
      say "本来就是原版 logd，已确保服务运行"
    fi
    ;;
  status)
    if is_bound; then
      echo "logd_stub=mounted"
      echo "mechanism=bind"
    elif is_stub_content; then
      echo "logd_stub=mounted"
      echo "mechanism=overlay"
    else
      echo "logd_stub=original"
      echo "mechanism=none"
    fi
    echo "service_state=$(getprop init.svc.logd)"
    echo "pid=$(pidof logd)"
    if [ -f "$CONF" ]; then
      echo "persisted=$(cat "$CONF" 2>/dev/null)"
    else
      echo "persisted=unset"
    fi
    ;;
  auto)
    # 供 service.sh 开机重放：只在显式开启过时才动手
    if [ -f "$CONF" ] && [ "$(cat "$CONF" 2>/dev/null)" = "on" ]; then
      use_stub && stop logd 2>/dev/null && say "开机重放: 已禁用"
    fi
    ;;
  *)
    echo "用法: $0 {on|off|status|auto}"
    exit 2
    ;;
esac
#!/system/bin/sh
# ui-tap.sh -- 用 uiautomator 点击（绕开 input tap 的 INJECT_EVENTS 限制）
#
# 背景：本机 KernelSU 拒绝了 `input tap` / `input keyevent`（INJECT_EVENTS 权限），
#       但 uiautomator 走的是无障碍服务通道，不受这个限制。
#
# 用法:
#   su -c "sh /data/local/tmp/ui-tap.sh text 前台"
#   su -c "sh /data/local/tmp/ui-tap.sh xy 161 847"
#   su -c "sh /data/local/tmp/ui-tap.sh dump"

PKG=com.powerlens.app
XML=/sdcard/ui_dump.xml

do_dump() {
  uiautomator dump "$XML" >/dev/null 2>&1
  cat "$XML"
}

case "$1" in
  text)
    # 按文本找元素并点击
    TARGET=$2
    [ -z "$TARGET" ] && { echo "需要文本"; exit 1; }
    D=$(do_dump)
    # 从 XML 里提取该文本的 bounds
    B=$(echo "$D" | tr '>' '\n' | grep -F "text=\"$TARGET\"" | grep -o 'bounds="\[[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*\]"' | head -n1)
    if [ -z "$B" ]; then
      echo "[未找到] text=\"$TARGET\""
      exit 1
    fi
    COORDS=$(echo "$B" | sed 's/bounds="\[\([0-9]*\),\([0-9]*\)\]\[\([0-9]*\),\([0-9]*\)\]"/\1 \2 \3 \4/')
    set -- $COORDS
    CX=$(( (${1} + ${3}) / 2 ))
    CY=$(( (${2} + ${4}) / 2 ))
    echo "  找到 '$TARGET' @ $B -> 点击 ($CX, $CY)"
    # uiautomator 无法直接点击，用 input tap（可能仍被拒）
    input tap "$CX" "$CY" 2>&1
    ;;
  xy)
    X=$2; Y=$3
    echo "  点击 ($X, $Y)"
    input tap "$X" "$Y" 2>&1
    ;;
  dump)
    do_dump | tr '>' '\n' | grep -E 'class="android.widget.(Spinner|Button)"|text="[^"]{1,20}"' | head -n 40
    ;;
  *)
    echo "用法: $0 {text TARGET|xy X Y|dump}"
    ;;
esac
#!/system/bin/sh
# install-boot.sh -- 安装/更新开机自启脚本（脚本内每条命令显式 su）
# 用法: su -c "sh /data/local/tmp/install-boot.sh"

SRC=/data/local/tmp/40-plc.sh
DST=/data/adb/service.d/40-plc.sh

echo "== 源文件 =="
ls -l "$SRC" 2>&1 | sed 's/^/  /'

echo "== 复制（显式 su） =="
su -c "cp -f $SRC $DST" 2>&1
echo "  cp rc=$?"

echo "== 设权限（显式 su） =="
su -c "chmod 755 $DST" 2>&1
echo "  chmod rc=$?"

echo "== 设属主（显式 su） =="
su -c "chown 0:0 $DST" 2>&1

echo "== 验证 =="
su -c "ls -l $DST" 2>&1 | sed 's/^/  /'
echo "  --- 关键内容 ---"
su -c "grep -E 'INTERVAL=|STORE=|DAEMON=' $DST" 2>&1 | sed 's/^/  /'

echo
echo "== service.d 全貌 =="
su -c "ls -l /data/adb/service.d/" 2>&1 | sed 's/^/  /'
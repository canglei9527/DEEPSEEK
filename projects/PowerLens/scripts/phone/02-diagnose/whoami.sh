#!/system/bin/sh
# whoami.sh -- 检查当前身份（用于诊断 su 是否生效）
/system/bin/id
echo "---"
echo "su 可用性: $(command -v su)"
echo "PATH=$PATH"
echo "---"
# 测试能否访问 app 私有目录
/system/bin/ls -ld /data/data/com.powerlens.app 2>&1
#!/system/bin/sh
# test-ab.sh -- 息屏待机  s2idle vs deep  无人值守 A/B（小米 15 Pro / HyperOS / KernelSU）
#
# 用法（PC 端）:
#   adb push test-ab.sh /data/local/tmp/
#   adb shell su -c "chmod 755 /data/local/tmp/test-ab.sh"
#   adb shell su -c "setsid sh /data/local/tmp/test-ab.sh 600 3 >/dev/null 2>&1 &"
#     $1 = 每个窗口秒数（默认 600）
#     $2 = 每种模式跑几轮（默认 3）
#
# 它会自己等到「拔电 + 息屏」才开始，跑完自动把 mem_sleep 还原成原来的值。
# 输出:
#   /data/local/tmp/ab-test.csv   每 30 s 一行的原始采样
#   /data/local/tmp/ab-test.log   人读的窗口汇总
#
# 判读要点：看「PM: suspend entry 次数 / 秒」和 fail 计数。
#   旧结论：s2idle 下 0.3–0.6 s 一次 entry，被 timerfd 唤醒源打断，AP 醒着 52–62%。
#   如果 deep 下 entry 次数骤降、current_now 绝对值明显变小 → deep 是该机型的正解。

WIN=${1:-600}
ROUNDS=${2:-3}
CSV=/data/local/tmp/ab-test.csv
LOG=/data/local/tmp/ab-test.log
SAMPLE_EVERY=30

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

[ -w /sys/power/mem_sleep ] || { log "FATAL: /sys/power/mem_sleep 不可写（没有 root？）"; exit 1; }
grep -q deep /sys/power/mem_sleep || { log "FATAL: 内核不支持 deep: $(cat /sys/power/mem_sleep)"; exit 1; }

ORIG=$(cat /sys/power/mem_sleep | sed 's/.*\[\(.*\)\].*/\1/')
log "start  WIN=${WIN}s ROUNDS=${ROUNDS}  原始 mem_sleep=${ORIG}  可选=$(cat /sys/power/mem_sleep)"

is_charging() {
    dumpsys battery 2>/dev/null | grep -q 'powered: true'
}

is_asleep() {
    dumpsys power 2>/dev/null | grep -q 'mWakefulness=Asleep'
}

# ---- 等到「拔电 + 息屏」 ----
log "等待拔电+息屏（每 30 s 轮询，最多 6 h）…"
n=0
while [ $n -lt 720 ]; do
    if ! is_charging && is_asleep; then
        log "条件满足：Discharging + Asleep"
        break
    fi
    n=$((n+1))
    sleep 30
done
[ $n -ge 720 ] && { log "超时：一直没等到拔电+息屏，退出"; exit 2; }

snap() {
    CUR=$(cat /sys/class/power_supply/battery/current_now 2>/dev/null)
    VOL=$(cat /sys/class/power_supply/battery/voltage_now 2>/dev/null)
    CAP=$(cat /sys/class/power_supply/battery/capacity 2>/dev/null)
    ENT=$(dmesg 2>/dev/null | grep -c 'PM: suspend entry')
    FAIL=$(cat /sys/power/suspend_stats/fail 2>/dev/null)
    LFD=$(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null | tr '\n' '/')
    RES=$(cat /sys/kernel/wakeup_reasons/last_resume_reason 2>/dev/null | tr '\n' '/')
    TC=$(cat /sys/class/power_supply/battery/charge_counter 2>/dev/null)
}

set_mode() {
    echo "$1" > /sys/power/mem_sleep 2>/dev/null
    sleep 2
    log "  mem_sleep -> $(cat /sys/power/mem_sleep)"
}

[ -f "$CSV" ] || echo "mode,round,epoch,current_uA,voltage_uV,capacity,charge_counter,suspend_entry_total,fail_total,last_failed_dev,last_resume_reason" > "$CSV"

for r in 1 2 3 4 5 6 7 8 9 10; do
    [ $r -gt $ROUNDS ] && break
    for MODE in deep s2idle; do
        set_mode "$MODE"
        T0=$(date +%s)
        snap
        ENT0=$ENT; FAIL0=$FAIL; SUM=0; CNT=0; TMPMAX=0
        LEFT=$WIN
        while [ $LEFT -gt 0 ]; do
            sleep $SAMPLE_EVERY
            snap
            echo "$MODE,$r,$(date +%s),$CUR,$VOL,$CAP,$TC,$ENT,$FAIL,$LFD,$RES" >> "$CSV"
            [ -n "$CUR" ] && SUM=$((SUM + CUR))
            CNT=$((CNT + 1))
            LEFT=$((LEFT - SAMPLE_EVERY))
        done
        T1=$(date +%s)
        snap
        ENT_D=$((ENT - ENT0)); FAIL_D=$((FAIL - FAIL0))
        DT=$((T1 - T0)); [ $DT -le 0 ] && DT=$WIN
        if [ $CNT -gt 0 ]; then MEAN=$((SUM / CNT)); else MEAN=0; fi
        # 每秒挂起尝试次数 / 每秒失败次数（0.01 精度用 awk 算，免得 shell 只有整数）
        RATE=$(awk "BEGIN{printf \"%.3f %.3f\", $ENT_D/$DT, $FAIL_D/$DT}")
        log "RESULT mode=$MODE round=$r dt=${DT}s mean_current=${MEAN}uA entry+=$ENT_D fail+=$FAIL_D last_failed_dev=$LFD resume=$RES"
        log "       per-second: entry/fail = $RATE"
    done
done

set_mode "$ORIG"
log "done，mem_sleep 已还原为 ${ORIG}"
log "原始数据: $CSV"

#!/system/bin/sh
# plc-daemon.sh -- PowerLens 采集守护 v6（维度归档与失败统计）
#
# ===== v4 的问题 =====
#   用 tick 计数决定何时跑 dims：
#     MOD=$((CPU_TICK % DIM_EVERY))
#   但每轮实际耗时 = CPU_INT + 采集耗时 + su 开销，实测 71.8 秒而非 60 秒。
#   导致 20 * 71.8 = 24 分钟才跑一次 dims（设定是 20 分钟）。
#   而且守护重启后 tick 归零，dims 会推迟很久。
#
# ===== v5 做法：绝对时间调度 =====
#   记录「上次 dims 的 epoch 秒」，每轮比较当前时间：
#     if [ $((NOW - LAST_DIM)) -ge $DIM_INT ]; then run_dims; fi
#   这样无论每轮耗时多少，dims 都按真实时间间隔触发。
#   同时也修了重启后 tick 归零的问题。
#
# ===== 采集调用必须带 su（实测）=====
#   守护脚本本身是 root（su -c "sh daemon.sh" 启动），
#   但它 fork 出的子进程会掉回 shell 身份。
#   实测：直接调用 plcollect → 0 UID / charge=-1（读不到 /proc/uid_cputime）
#         su -c 调用      → 156 UID 正常
#   所以每条采集命令都必须显式 su -c，开销 26 ms/次，可接受。
#
# ===== 用法 =====
#   su -c "setsid sh /data/local/tmp/plc/plc-daemon.sh [CPU间隔] [维度间隔]"
#   默认 60 1200

CPU_INT=${1:-60}
DIM_INT=${2:-1200}

BIN=/data/local/tmp/plc/plcollect
DIMBIN=/data/local/tmp/plc/pldims
STORE=/data/local/tmp/powerlens/snapshots.tsv
DIMS=/data/local/tmp/powerlens/dims.tsv
DIMS_DIR=/data/local/tmp/powerlens
DIMS_ARCHIVE_DIR=/data/local/tmp/plc/dims-history
LOG=/data/local/tmp/plc/daemon.log
PIDF=/data/local/tmp/plc/daemon.pid

[ -x "$BIN" ] || { echo "$(date '+%F %T') FATAL: 缺 $BIN" >> $LOG; exit 1; }

# ---- 单实例保护 ----
if [ -f "$PIDF" ]; then
  OLD=$(cat "$PIDF" 2>/dev/null)
  if [ -n "$OLD" ] && [ -d "/proc/$OLD" ]; then
    CMD=$(tr -d '\000' < "/proc/$OLD/cmdline" 2>/dev/null)
    case "$CMD" in
      *plc-daemon*) echo "$(date '+%F %T') 已有实例 pid=$OLD，退出" >> $LOG; exit 0 ;;
    esac
  fi
fi
echo $$ > "$PIDF"

mkdir -p /data/local/tmp/powerlens "$DIMS_ARCHIVE_DIR" 2>/dev/null
chmod 777 /data/local/tmp/powerlens 2>/dev/null
chmod 700 "$DIMS_ARCHIVE_DIR" 2>/dev/null
chmod 666 "$STORE" "$DIMS" 2>/dev/null

echo "$(date '+%F %T') plc-daemon v6 start pid=$$ cpu=${CPU_INT}s dim=${DIM_INT}s" >> "$LOG"

# ---- 绝对时间调度 ----
NOW=$(date +%s)
LAST_DIM=$((NOW - DIM_INT))    # 无历史基线时，第一轮立即建基线
LAST_GLOBAL_MS=$(cut -f1 "$DIMS_DIR/dims.global" 2>/dev/null)
case "$LAST_GLOBAL_MS" in
  ''|*[!0-9]*) ;;
  *)
    if [ "$LAST_GLOBAL_MS" -gt 0 ] 2>/dev/null; then
      LAST_DIM=$((LAST_GLOBAL_MS / 1000))
      [ "$LAST_DIM" -gt "$NOW" ] && LAST_DIM=$((NOW - DIM_INT))
    fi
    ;;
esac
N_CPU=0
N_CPU_FAIL=0
N_DIM=0
N_DIM_FAIL=0
N_ARCHIVE_FAIL=0
ALIVE_AT=$NOW

while true; do
  NOW=$(date +%s)

  # ---- 高频：CPU / IO ----
  if su -c "$BIN --once -f $STORE -q" >/dev/null 2>&1; then
    chmod 666 "$STORE" 2>/dev/null
    N_CPU=$((N_CPU + 1))
  else
    RC=$?
    N_CPU_FAIL=$((N_CPU_FAIL + 1))
    echo "$(date '+%F %T') CPU FAIL rc=$RC" >> "$LOG"
  fi

  # ---- 低频：维度（按绝对时间判断）----
  if [ $((NOW - LAST_DIM)) -ge "$DIM_INT" ] && [ -x "$DIMBIN" ]; then
    OUT=$(su -c "$DIMBIN -f $DIMS" 2>&1)
    RC=$?
    case "$OUT" in
      *增量*|*基线*)
        if [ "$RC" -eq 0 ]; then
          echo "$(date '+%F %T') dims ok: $OUT" >> "$LOG"
          N_DIM=$((N_DIM + 1))

          # pldims intentionally rewrites dims.tsv on each run. Preserve the
          # successful interval before the next run replaces it. Keep private,
          # append-only UID and run-metadata files per local calendar day.
          DIM_DAY=$(date '+%Y%m%d')
          DIM_RUNS="$DIMS_ARCHIVE_DIR/dims-runs-$DIM_DAY.tsv"
          RUN_TS=$(awk -F '\t' 'NF { ts=$1 } END { print ts }' "$DIMS_DIR/dims.global" 2>/dev/null)
          RUN_GLOBAL_CHECK=$(awk -F '\t' 'NF != 15 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ { bad=1 } END { if (NR == 1 && !bad) print "ok"; else print "bad" }' "$DIMS_DIR/dims.global" 2>/dev/null)
          RUN_LAST_TS=$(tail -n 1 "$DIM_RUNS" 2>/dev/null | cut -f1)
          if [ "$RUN_GLOBAL_CHECK" = "ok" ] && [ -n "$RUN_TS" ] && [ "$RUN_TS" != "$RUN_LAST_TS" ]; then
            if cat "$DIMS_DIR/dims.global" >> "$DIM_RUNS"; then
              chmod 600 "$DIM_RUNS" 2>/dev/null
            else
              N_ARCHIVE_FAIL=$((N_ARCHIVE_FAIL + 1))
              echo "$(date '+%F %T') dims metadata archive FAIL file=$DIM_RUNS" >> "$LOG"
            fi
          else
            if [ "$RUN_TS" = "$RUN_LAST_TS" ]; then
              echo "$(date '+%F %T') dims metadata archive skipped duplicate ts=$RUN_TS" >> "$LOG"
            else
              N_ARCHIVE_FAIL=$((N_ARCHIVE_FAIL + 1))
              echo "$(date '+%F %T') dims metadata archive FAIL format=$RUN_GLOBAL_CHECK ts=$RUN_TS" >> "$LOG"
            fi
          fi

          DIM_TS=$(awk -F '\t' 'NF { ts=$1 } END { print ts }' "$DIMS" 2>/dev/null)
          DIM_ROWS=$(wc -l < "$DIMS" 2>/dev/null)
          DIM_CHECK=$(awk -F '\t' 'NF { rows++; if (NF != 22) bad=1; if (rows == 1) { ts=$1; interval=$9 } else if ($1 != ts || $9 != interval) bad=1 } END { if (!rows) print "empty"; else if (bad) print "bad"; else print "ok" }' "$DIMS" 2>/dev/null)
          DIM_HIST="$DIMS_ARCHIVE_DIR/dims-history-$DIM_DAY.tsv"
          HIST_TS=$(tail -n 1 "$DIM_HIST" 2>/dev/null | cut -f1)
          if [ "$DIM_CHECK" = "empty" ]; then
            echo "$(date '+%F %T') dims empty ts=$RUN_TS interval=$(cut -f2 "$DIMS_DIR/dims.global" 2>/dev/null) rows=0" >> "$LOG"
          elif [ "$DIM_CHECK" = "ok" ] && [ -n "$DIM_TS" ] && [ "$DIM_TS" = "$RUN_TS" ] && [ "$DIM_TS" != "$HIST_TS" ]; then
            if cat "$DIMS" >> "$DIM_HIST"; then
              chmod 600 "$DIM_HIST" 2>/dev/null
              echo "$(date '+%F %T') dims archived ts=$DIM_TS interval=$(cut -f2 "$DIMS_DIR/dims.global" 2>/dev/null) rows=$DIM_ROWS file=$DIM_HIST" >> "$LOG"
            else
              N_ARCHIVE_FAIL=$((N_ARCHIVE_FAIL + 1))
              echo "$(date '+%F %T') dims archive FAIL file=$DIM_HIST" >> "$LOG"
            fi
          elif [ "$DIM_TS" = "$HIST_TS" ] && [ -n "$DIM_TS" ]; then
            echo "$(date '+%F %T') dims archive skipped duplicate ts=$DIM_TS" >> "$LOG"
          else
            N_ARCHIVE_FAIL=$((N_ARCHIVE_FAIL + 1))
            echo "$(date '+%F %T') dims archive FAIL format=$DIM_CHECK dims_ts=$DIM_TS global_ts=$RUN_TS" >> "$LOG"
          fi
        else
          N_DIM_FAIL=$((N_DIM_FAIL + 1))
          echo "$(date '+%F %T') dims FAIL rc=$RC: $OUT" >> "$LOG"
        fi
        ;;
      *)
        N_DIM_FAIL=$((N_DIM_FAIL + 1))
        echo "$(date '+%F %T') dims FAIL rc=$RC: $OUT" >> "$LOG"
        ;;
    esac
    chmod 666 "$DIMS" 2>/dev/null
    LAST_DIM=$NOW
  fi

  # ---- 心跳（每 30 分钟一次）----
  if [ $((NOW - ALIVE_AT)) -ge 1800 ]; then
    echo "$(date '+%F %T') alive cpu_ok=$N_CPU cpu_fail=$N_CPU_FAIL dims_ok=$N_DIM dims_fail=$N_DIM_FAIL archive_fail=$N_ARCHIVE_FAIL snap=$(wc -l < $STORE 2>/dev/null)L dims=$(wc -l < $DIMS 2>/dev/null)L next_dim=$(( DIM_INT - (NOW - LAST_DIM) ))s" >> $LOG
    ALIVE_AT=$NOW
  fi

  # ---- 睡眠：对齐到下一个整周期，避免累积漂移 ----
  NOW2=$(date +%s)
  ELAPSED=$((NOW2 - NOW))
  SLEEP=$((CPU_INT - ELAPSED))
  [ "$SLEEP" -lt 1 ] && SLEEP=1
  sleep "$SLEEP"
done

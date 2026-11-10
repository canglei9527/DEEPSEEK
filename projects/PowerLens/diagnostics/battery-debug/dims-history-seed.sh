#!/system/bin/sh
# One-time seed of the current valid pldims interval before daemon replacement.
D=/data/local/tmp/powerlens
A=/data/local/tmp/plc/dims-history

mkdir -p "$A" || exit 1
chmod 700 "$A" || exit 1
DAY=$(date '+%Y%m%d')

RUNS="$A/dims-runs-$DAY.tsv"
RUN_TS=$(awk -F '\t' 'NF { ts=$1 } END { print ts }' "$D/dims.global" 2>/dev/null)
RUN_OK=$(awk -F '\t' 'NF != 15 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ { bad=1 } END { if (NR == 1 && !bad) print "ok"; else print "bad" }' "$D/dims.global" 2>/dev/null)
RUN_LAST=$(tail -n 1 "$RUNS" 2>/dev/null | cut -f1)
if [ "$RUN_OK" = "ok" ] && [ -n "$RUN_TS" ] && [ "$RUN_TS" != "$RUN_LAST" ]; then
  cat "$D/dims.global" >> "$RUNS" || exit 2
  chmod 600 "$RUNS" || exit 3
fi

HIST="$A/dims-history-$DAY.tsv"
DIM_TS=$(awk -F '\t' 'NF { ts=$1 } END { print ts }' "$D/dims.tsv" 2>/dev/null)
DIM_CHECK=$(awk -F '\t' 'NF { rows++; if (NF != 22) bad=1; if (rows == 1) { ts=$1; interval=$9 } else if ($1 != ts || $9 != interval) bad=1 } END { if (!rows) print "empty"; else if (bad) print "bad"; else print "ok" }' "$D/dims.tsv" 2>/dev/null)
HIST_LAST=$(tail -n 1 "$HIST" 2>/dev/null | cut -f1)
if [ "$DIM_CHECK" = "ok" ] && [ -n "$DIM_TS" ] && [ "$DIM_TS" = "$RUN_TS" ] && [ "$DIM_TS" != "$HIST_LAST" ]; then
  cat "$D/dims.tsv" >> "$HIST" || exit 4
  chmod 600 "$HIST" || exit 5
fi

echo "global_check=$RUN_OK global_ts=$RUN_TS uid_check=$DIM_CHECK uid_ts=$DIM_TS uid_rows=$(wc -l < "$D/dims.tsv" 2>/dev/null)"
ls -l "$A"/dims-*.tsv 2>/dev/null

#!/usr/bin/env bash
# Lab 4 parameter sweep: synthesise every (taps, channels, accumulator width)
# point for the ternary design and the INT4 multiplier baseline, map to the
# GF180MCU 7-track standard cells and record the cell area.
#
#   usage: synth/sweep.sh path/to/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib
# With a PDK installed by the TT tools the Liberty file is under
#   $PDK_ROOT/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/liberty/
set -euo pipefail
LIB=${1:?"give the GF180 Liberty file"}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/synth/results
mkdir -p "$OUT"
CSV=$OUT/sweep.csv
echo "design,taps,channels,accw,depth,cells,flops,area_um2,pct_of_tile" > "$CSV"
TILE=55867   # 1x1 GF180 tile, 347 x 161 um
DEPTH=${DEPTH:-4}
for base in 0 1; do
  for taps in 3 5 7; do
    for ch in 1 2; do
      for accw in 12 16 20; do
        tag=$([ $base = 1 ] && echo int4 || echo ternary)_t${taps}_c${ch}_a${accw}
        yosys -q -l "$OUT/$tag.log" -p "
          read_verilog -sv $ROOT/src/tconv_core.v $ROOT/src/project.v
          chparam -set NTAPS $taps -set NCH $ch -set ACCW $accw -set DEPTH $DEPTH -set BASELINE $base tt_um_mariamku123_ternary_conv
          synth -top tt_um_mariamku123_ternary_conv -flatten
          dfflegalize -cell \$_DFF_P_ 01
          dfflibmap -liberty $LIB
          abc -liberty $LIB
          opt_clean
          tee -o $OUT/$tag.stat stat -liberty $LIB" >/dev/null
        cells=$(grep -m1 -E "Number of cells|cells$" "$OUT/$tag.stat" | grep -oE '[0-9]+' | head -1)
        flops=$(grep -E "__dff" "$OUT/$tag.stat" | awk '{s+=$2} END {print s+0}')
        area=$(grep -m1 "Chip area" "$OUT/$tag.stat" | grep -oE '[0-9]+(\.[0-9]+)?' | tail -1)
        pct=$(python3 -c "print(round(100*$area/$TILE,1))")
        echo "$( [ $base = 1 ] && echo int4 || echo ternary ),$taps,$ch,$accw,$DEPTH,$cells,$flops,$area,$pct" | tee -a "$CSV"
      done
    done
  done
done

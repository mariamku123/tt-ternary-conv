#!/usr/bin/env bash
# Lab 1 evidence: tool versions, inverter simulation, generic and GF180-mapped synthesis.
# Run from the repo root inside Ubuntu (WSL):  bash labs/lab01/run_lab01.sh
set -u
OUT=labs/lab01/evidence
mkdir -p "$OUT" build
{
  echo "date: $(date -Is)"
  echo "commit: $(git rev-parse HEAD)"
  echo "iverilog: $(iverilog -V 2>&1 | head -1)"
  echo "yosys: $(yosys -V)"
  echo "python: $(python3 --version)"
  echo "docker: $(docker --version 2>&1)"
  echo "librelane: $(python3 -m pip show librelane 2>/dev/null | grep Version)"
  echo "PDK: ${PDK:-gf180mcuD}  PDK_ROOT: ${PDK_ROOT:-unset}"
} | tee "$OUT/versions.txt"
iverilog -g2012 -s tb_inverter -o build/inverter.vvp labs/lab01/inverter.v labs/lab01/tb_inverter.v
vvp build/inverter.vvp | tee "$OUT/sim.txt"
yosys -p "read_verilog labs/lab01/inverter.v; synth -top inverter; stat" | tee "$OUT/synth_generic.txt"
LIB=$(ls "${PDK_ROOT:-$HOME/ccen621/pdk}"/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/liberty/*tt_025C_5v00.lib 2>/dev/null | head -1)
if [ -n "$LIB" ]; then
  yosys -p "read_verilog labs/lab01/inverter.v; synth -top inverter; abc -liberty $LIB; stat -liberty $LIB" \
    | tee "$OUT/synth_gf180.txt"
else
  echo "GF180 Liberty not found yet: run the harden step once so the PDK is downloaded" | tee "$OUT/synth_gf180.txt"
fi

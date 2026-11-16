## How it works

A streaming 1-D convolution engine, `y[n][c] = sum_k w[c][k] * x[n-k]`, with ternary weights {-1, 0, +1}.
Each tap adds, subtracts or skips its activation, so there are no multipliers. Taps whose weight or activation
is zero keep their operand register frozen (operand isolation), which cuts switching activity while the
latency stays a fixed two cycles. Results are summed with saturation to an `ACCW`-bit accumulator and kept
in a small buffer that the host reads back over the bidirectional pins.

Parameters: `NTAPS` (3/5/7), `NCH` (1/2), `ACCW` (12/16/20), `DEPTH` (result buffer), `BASELINE`
(1 = signed INT4 multiplier version for comparison). The full packet protocol is in [protocol.md](protocol.md).

## How to test

1. Reset. `uo_out` reads `0x80` (buffer empty).
2. LOADW the packed weights, CONFIG if needed, then EXEC one activation per clock.
3. Send READ with `start`, sample `uio_out` each cycle while sending READ, then NOP to release the bus.

## External hardware

None. Any microcontroller or the Tiny Tapeout demo board can drive the pins.

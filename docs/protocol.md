# Host protocol

One packet per clock. A packet is accepted on the rising edge when `valid` is high.

| Pin | Bits | Meaning |
|---|---|---|
| `ui_in[7:5]` | opcode | 0 NOP, 1 CONFIG, 2 LOADW, 3 EXEC, 4 READ, 5 CLEAR, 6 DIAG, 7 reserved (error) |
| `ui_in[4:3]` | precision | activations: 0 INT8, 1 INT4 (`uio_in[3:0]`), 2 INT2 (`uio_in[1:0]`), 3 reserved (error) |
| `ui_in[2]` | valid | packet present |
| `ui_in[1]` | last | last byte of a weight load; on CLEAR selects REWIND |
| `ui_in[0]` | start | first byte of a LOADW / EXEC / READ / DIAG burst |
| `uio_in[7:0]` | data | CONFIG byte, packed weights, or one activation |
| `uio_out[7:0]` | data | read-back byte while `rd_mode`, otherwise 0 |
| `uio_oe[7:0]` | | `0xFF` only in `rd_mode`, otherwise `0x00` |
| `uo_out` | status | [0] ready, [1] busy, [2] done, [3] error, [4] rd_mode, [5] saturated, [6] weights loaded, [7] buffer empty |

## Commands

- **CONFIG**: `uio_in[3:0]` output shift, `[4]` 8-bit output mode, `[5]` accumulate, `[6]` zero skipping (1 after reset).
- **LOADW**: weights in channel-major order (`c*NTAPS + k`), least-significant code first.
  Ternary: 2-bit codes, 4 per byte, `00`=0, `01`=+1, `11`=-1, `10`=illegal (error, treated as 0).
  INT4 baseline: two's-complement nibbles, 2 per byte. `start` on the first byte, `last` on the final one.
  Ending early raises an error; extra bytes raise an error.
- **EXEC**: one activation per packet. `start` zero-pads the window (new sequence) and restarts the result buffer.
  Each sample produces `NCH` results two cycles later (fixed latency). `busy` is high while a result is pending.
  With accumulate on, results are added to the stored ones (e.g. summing input channels).
- **READ**: `start` puts the chip in `rd_mode`; from the next cycle it drives byte 0 on `uio_out`.
  Each further READ packet advances one byte. Order: sample, then channel, then byte (LSB first).
  Full-width words are `ceil(ACCW/8)` bytes, sign-extended. `done` marks the last byte.
- **CLEAR**: clears results, window, flags and errors (weights and config are kept).
  With `last=1` it is a **REWIND**: only the buffer pointer restarts, so a long stream can be processed in
  `DEPTH`-sized pieces without losing the window.
- **DIAG**: like READ, returns 8 bytes: `0xA9`, `{NCH,NTAPS}`, `ACCW`, `DEPTH`, `{BASELINE,000,err_code}`,
  config, buffer count, `{weights_loaded,0000000}`.

## Bus turnaround

1. The host stops driving `uio` before sending READ/DIAG `start` (those packets carry no data).
2. The chip drives `uio` from the next cycle (`uo_out[4]` = 1).
3. To finish, the host sends NOP (or CLEAR). The chip releases `uio` on that edge.
4. The host may drive `uio` again once `uo_out[4]` = 0. A CONFIG/LOADW/EXEC sent while the chip still drives
   is a contention error (code 8) and is ignored.

## Error codes (DIAG byte 4)

1 reserved opcode, 2 reserved precision, 3 EXEC before weights, 4 buffer full, 5 too many weight bytes,
6 illegal ternary code, 7 weight load ended early, 8 bus contention, 9 read past end, 10 READ/DIAG continuation
without start. `error` is sticky until CLEAR.

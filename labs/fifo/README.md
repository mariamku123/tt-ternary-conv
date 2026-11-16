# Lab-manual FIFO demonstrator (required, not graded)

8-bit, 4-entry synchronous FIFO with the manual's pin mapping (`uio_oe = 0xC7`).
It lives here so it does not change the project's Tiny Tapeout top module.

- `fifo8_core.v`: core. Policies are in the header comment (first-word fall-through,
  overflow unless a read frees space in the same cycle, underflow on empty read, sticky error).
- `tt_um_ccen621_fifo.v`: TT shell. `ui_in` = DIN, `uo_out` = DOUT,
  `uio[0..2]` full/empty/valid, `uio[3..5]` write/read/clear_error, `uio[6..7]` error/almost_full.
- `test/`: cocotb scoreboard tests: walking 1/0, 0x80/0xFF, full-to-empty sweep, pointer wrap,
  reset under load, 400 random transactions with coverage, and assertions on count/empty/full.

Run: `cd labs/fifo/test && make`
Lab 2 check: `yosys -Q -p "read_verilog -sv labs/fifo/fifo8_core.v; hierarchy -top fifo8_core; proc; check; stat"`

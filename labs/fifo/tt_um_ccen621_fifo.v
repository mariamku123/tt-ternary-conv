// Tiny Tapeout shell for the lab FIFO, pin mapping from the lab manual section 2.
// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module tt_um_ccen621_fifo (
    input  wire [7:0] ui_in,    // DIN
    output wire [7:0] uo_out,   // DOUT
    input  wire [7:0] uio_in,   // [3] write, [4] read, [5] clear_error
    output wire [7:0] uio_out,  // [0] full, [1] empty, [2] valid, [6] error, [7] almost_full
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  wire full, empty, valid, almost_full, error;
  fifo8_core core (
      .clk(clk), .rst_n(rst_n), .din(ui_in),
      .write(uio_in[3]), .read(uio_in[4]), .clear_error(uio_in[5]),
      .dout(uo_out), .full(full), .empty(empty), .valid(valid),
      .almost_full(almost_full), .error(error)
  );
  assign uio_out = {almost_full, error, 3'b000, valid, empty, full};
  assign uio_oe  = 8'hC7;
  wire _unused = &{ena, uio_in[7:6], uio_in[2:0], 1'b0};
endmodule

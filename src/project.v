/*
 * Copyright (c) 2026 mariam
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tiny Tapeout wrapper for the ternary CNN convolution processor.
 */
`default_nettype none

module tt_um_mariamku123_ternary_conv #(
    parameter integer NTAPS    = 3,
    parameter integer NCH      = 1,
    parameter integer ACCW     = 16,
    parameter integer DEPTH    = 4,
    parameter integer BASELINE = 0
) (
    input  wire [7:0] ui_in,    // opcode[7:5], precision[4:3], valid, last, start
    output wire [7:0] uo_out,   // status
    input  wire [7:0] uio_in,   // host data: config, weights, activations
    output wire [7:0] uio_out,  // read-back data
    output wire [7:0] uio_oe,   // 0xFF only while the ASIC drives read data
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  tconv_core #(
      .NTAPS(NTAPS), .NCH(NCH), .ACCW(ACCW), .DEPTH(DEPTH), .BASELINE(BASELINE)
  ) core (
      .clk    (clk),
      .rst_n  (rst_n),
      .ui_in  (ui_in),
      .uio_in (uio_in),
      .uo_out (uo_out),
      .uio_out(uio_out),
      .uio_oe (uio_oe)
  );

  wire _unused = &{ena, 1'b0};

endmodule

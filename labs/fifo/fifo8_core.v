// Lab-manual demonstrator (required, not graded): 8-bit, 4-entry synchronous FIFO.
// SPDX-License-Identifier: Apache-2.0
// Policies:
//  - first-word fall-through: dout shows the head entry whenever valid = 1
//  - write while full: overflow error, data dropped, unless read is also high (then both happen)
//  - read while empty: underflow error, nothing happens (a simultaneous write still enqueues)
//  - error is sticky until clear_error
`default_nettype none
module fifo8_core #(
    parameter integer WIDTH = 8,
    parameter integer DEPTH = 4
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire [WIDTH-1:0] din,
    input  wire             write,
    input  wire             read,
    input  wire             clear_error,
    output wire [WIDTH-1:0] dout,
    output wire             full,
    output wire             empty,
    output wire             valid,
    output wire             almost_full,
    output reg              error
);
  localparam integer AW = $clog2(DEPTH);
  reg [WIDTH-1:0] mem [0:DEPTH-1];
  reg [AW-1:0]    wptr, rptr;
  reg [AW:0]      count;

  assign full        = (count == DEPTH);
  assign empty       = (count == 0);
  assign valid       = ~empty;
  assign almost_full = (count >= DEPTH - 1);
  assign dout        = empty ? {WIDTH{1'b0}} : mem[rptr];

  wire do_read  = read && !empty;
  wire do_write = write && (!full || do_read);

  integer i;
  always @(posedge clk) begin
    if (!rst_n) begin
      wptr <= 0; rptr <= 0; count <= 0; error <= 1'b0;
      for (i = 0; i < DEPTH; i = i + 1) mem[i] <= {WIDTH{1'b0}};
    end else begin
      if (do_write) begin
        mem[wptr] <= din;
        wptr <= (wptr == DEPTH - 1) ? 0 : wptr + 1'b1;
      end
      if (do_read) rptr <= (rptr == DEPTH - 1) ? 0 : rptr + 1'b1;
      count <= count + do_write - do_read;
      if (clear_error)                       error <= 1'b0;
      else if ((write && !do_write) || (read && empty)) error <= 1'b1;
    end
  end
endmodule

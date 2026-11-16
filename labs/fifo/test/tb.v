`default_nettype none
`timescale 1ns / 1ps
module tb ();
  initial begin
    $dumpfile("fifo.fst");
    $dumpvars(0, tb);
  end
  reg clk, rst_n, ena;
  reg [7:0] ui_in, uio_in;
  wire [7:0] uo_out, uio_out, uio_oe;
  tt_um_ccen621_fifo dut (.ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out),
                          .uio_oe(uio_oe), .ena(ena), .clk(clk), .rst_n(rst_n));
  // assertions on the core state (Lab 3 step 4)
  always @(posedge clk) if (rst_n) begin
    if (dut.core.count > 4) $fatal(1, "count exceeds DEPTH");
    if (dut.core.empty !== (dut.core.count == 0)) $fatal(1, "empty != (count==0)");
    if (dut.core.full !== (dut.core.count == 4)) $fatal(1, "full != (count==DEPTH)");
  end
endmodule

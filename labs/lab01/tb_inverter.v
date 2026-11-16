// Lab 1 self-checking test for the inverter and the two-input gate
module tb_inverter;
  reg a, b; wire y, z;
  integer i;
  inverter dut(.a(a), .y(y));
  nand2    g  (.a(a), .b(b), .y(z));
  initial begin
    a=0; #1; if (y !== 1) $fatal(1, "inverter a=0 failed");
    a=1; #1; if (y !== 0) $fatal(1, "inverter a=1 failed");
    for (i = 0; i < 4; i = i + 1) begin
      {a, b} = i; #1;
      if (z !== ~(a & b)) $fatal(1, "nand2 failed for a=%0d b=%0d", a, b);
    end
    $display("PASS: inverter and nand2");
    $finish;
  end
endmodule

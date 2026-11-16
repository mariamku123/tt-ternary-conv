// Lab 1 warm-up (teacher supplied)
module inverter(input wire a, output wire y);
  assign y = ~a;
endmodule

module nand2(input wire a, input wire b, output wire y);
  assign y = ~(a & b);
endmodule

/*
 * Ternary CNN convolution processor (CCEN 621/785 project 9)
 * SPDX-License-Identifier: Apache-2.0
 *
 * Streaming 1-D convolution y[n][c] = sum_k w[c][k] * x[n-k].
 *  BASELINE = 0 : ternary weights {-1,0,+1}, add / subtract / skip, with
 *                 activation-zero skipping (operand isolation) at fixed latency.
 *  BASELINE = 1 : controlled baseline, signed INT4 weights with multipliers.
 * The host protocol is documented in docs/protocol.md.
 */
`default_nettype none

module tconv_core #(
    parameter integer NTAPS    = 7,   // 3, 5 or 7
    parameter integer NCH      = 2,   // 1 or 2 output channels
    parameter integer ACCW     = 16,  // 12, 16 or 20
    parameter integer DEPTH    = 4,   // result buffer depth (samples per channel)
    parameter integer BASELINE = 0
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    input  wire [7:0] uio_in,
    output wire [7:0] uo_out,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe
);
  // ------------------------------------------------------------------ sizes
  localparam integer WB      = BASELINE ? 4 : 2;      // bits per weight
  localparam integer NW      = NCH * NTAPS;           // number of weights
  localparam integer WPB     = 8 / WB;                // weights per byte
  localparam integer NWBYTES = (NW + WPB - 1) / WPB;  // bytes in a weight load
  localparam integer NB      = (ACCW + 7) / 8;        // bytes per full-width result
  localparam integer OPW     = BASELINE ? 12 : 9;     // operand register width
  localparam integer SUMW    = OPW + $clog2(NTAPS) + 1;
  localparam integer EXTW    = ((SUMW > ACCW) ? SUMW : ACCW) + 2;
  localparam integer DW      = $clog2(DEPTH + 1);
  localparam integer NBUF    = DEPTH * NCH;

  // ---------------------------------------------------------------- opcodes
  localparam [2:0] OP_NOP = 3'd0, OP_CONFIG = 3'd1, OP_LOADW = 3'd2, OP_EXEC = 3'd3,
                   OP_READ = 3'd4, OP_CLEAR = 3'd5, OP_DIAG = 3'd6;

  // ------------------------------------------------------------ error codes
  localparam [3:0] E_OPC = 4'd1, E_PREC = 4'd2, E_NOW = 4'd3, E_FULL = 4'd4,
                   E_WOVF = 4'd5, E_WCODE = 4'd6, E_WSHORT = 4'd7, E_CONT = 4'd8,
                   E_RDEND = 4'd9, E_PROTO = 4'd10;

  wire [2:0] op    = ui_in[7:5];
  wire [1:0] prec  = ui_in[4:3];
  wire       valid = ui_in[2];
  wire       last  = ui_in[1];
  wire       start = ui_in[0];

  // ------------------------------------------------------------------ state
  reg [WB-1:0]          w      [0:NW-1];     // weight codes
  reg [7:0]             win    [0:NTAPS-1];  // activation shift window
  reg                   nzw    [0:NTAPS-1];  // non-zero flag per window slot
  reg signed [OPW-1:0]  opr    [0:NW-1];     // isolated tap operands
  reg                   msk    [0:NW-1];     // tap active mask
  reg [ACCW-1:0]        rbuf   [0:NBUF-1];   // result buffer

  reg [7:0]  wl_ptr;
  reg        w_loaded;
  reg [3:0]  cfg_shift;
  reg        cfg_out8, cfg_acc, cfg_zskip;
  reg        pend;
  reg [DW-1:0] wi;      // samples issued
  reg [DW-1:0] waddr;   // sample slot being written by stage 2
  reg        rd_mode, diag_mode;
  reg [2:0]  rb;        // byte within word (or diag index)
  reg [1:0]  rc;        // channel
  reg [DW-1:0] rs;      // sample
  reg        err, sat;
  reg [3:0]  err_code;

  // ------------------------------------------------- activation precision
  reg [7:0] a_ext;
  always @(*) begin
    case (prec)
      2'd0:    a_ext = uio_in;
      2'd1:    a_ext = {{4{uio_in[3]}}, uio_in[3:0]};
      2'd2:    a_ext = {{6{uio_in[1]}}, uio_in[1:0]};
      default: a_ext = 8'd0;
    endcase
  end

  // next window contents (what the window holds after an EXEC is accepted)
  reg [7:0] nwin [0:NTAPS-1];
  reg       nnz  [0:NTAPS-1];
  integer k, c, n;            // combinational loop indices
  integer sk, sc, sn;         // sequential loop indices (separate: shared integers make Yosys infer conflicting drivers)
  always @(*) begin
    nwin[0] = a_ext;
    nnz[0]  = |a_ext;
    for (k = 1; k < NTAPS; k = k + 1) begin
      nwin[k] = start ? 8'd0 : win[k-1];
      nnz[k]  = start ? 1'b0 : nzw[k-1];
    end
  end

  // ------------------------------------------------------- tap adder trees
  reg signed [SUMW-1:0] sum [0:NCH-1];
  reg                   anym[0:NCH-1];
  always @(*) begin
    for (c = 0; c < NCH; c = c + 1) begin
      sum[c]  = {SUMW{1'b0}};
      anym[c] = 1'b0;
      for (k = 0; k < NTAPS; k = k + 1) begin
        if (msk[c*NTAPS+k]) sum[c] = sum[c] + {{(SUMW-OPW){opr[c*NTAPS+k][OPW-1]}}, opr[c*NTAPS+k]};
        anym[c] = anym[c] | msk[c*NTAPS+k];
      end
    end
  end

  // ---------------------------------------------------- saturating update
  localparam signed [EXTW-1:0] SMAX = (1 <<< (ACCW - 1)) - 1;
  localparam signed [EXTW-1:0] SMIN = -(1 <<< (ACCW - 1));
  reg signed [EXTW-1:0] tot  [0:NCH-1];
  reg [ACCW-1:0]        totq [0:NCH-1];
  reg                   tsat [0:NCH-1];
  always @(*) begin
    for (c = 0; c < NCH; c = c + 1) begin
      tot[c] = {{(EXTW-SUMW){sum[c][SUMW-1]}}, sum[c]};
      if (cfg_acc)
        tot[c] = tot[c] + {{(EXTW-ACCW){rbuf[waddr*NCH+c][ACCW-1]}}, rbuf[waddr*NCH+c]};
      tsat[c] = (tot[c] > SMAX) || (tot[c] < SMIN);
      if (tot[c] > SMAX)      totq[c] = SMAX[ACCW-1:0];
      else if (tot[c] < SMIN) totq[c] = SMIN[ACCW-1:0];
      else                    totq[c] = tot[c][ACCW-1:0];
    end
  end

  // ------------------------------------------------------------- readback
  wire [DW-1:0] nbo_last_s = wi - 1'b1;
  wire [2:0]    nbo        = cfg_out8 ? 3'd1 : NB[2:0];
  wire at_last = (rs == nbo_last_s) && (rc == NCH - 1) && (rb == nbo - 1'b1);
  wire full    = (wi == DEPTH);

  wire [ACCW-1:0] rword = rbuf[rs*NCH+rc];
  wire signed [ACCW-1:0] rshift = $signed(rword) >>> cfg_shift;
  reg  [7:0] rbyte;
  reg  [NB*8-1:0] rwide;
  always @(*) begin
    rwide = {{(NB*8-ACCW){rword[ACCW-1]}}, rword};
    if (cfg_out8) begin
      if (rshift > 127)       rbyte = 8'h7F;
      else if (rshift < -128) rbyte = 8'h80;
      else                    rbyte = rshift[7:0];
    end else begin
      rbyte = rwide[rb*8+:8];
    end
  end

  reg [7:0] dbyte;
  always @(*) begin
    case (rb)
      3'd0: dbyte = 8'hA9;
      3'd1: dbyte = {NCH[3:0], NTAPS[3:0]};
      3'd2: dbyte = ACCW[7:0];
      3'd3: dbyte = DEPTH[7:0];
      3'd4: dbyte = {BASELINE[0], 3'b000, err_code};
      3'd5: dbyte = {cfg_zskip, cfg_acc, cfg_out8, 1'b0, cfg_shift};
      3'd6: dbyte = {{(8-DW){1'b0}}, wi};
      default: dbyte = {w_loaded, 7'd0};
    endcase
  end

  assign uio_out = rd_mode ? (diag_mode ? dbyte : rbyte) : 8'h00;
  assign uio_oe  = rd_mode ? 8'hFF : 8'h00;
  assign uo_out  = {(wi == 0), w_loaded, sat, rd_mode, err,
                    rd_mode ? at_last : full, pend, w_loaded & ~full & ~rd_mode};

  // -------------------------------------------------- weight byte decoding
  // Which weight indices does the current load byte cover?
  wire [7:0] lidx = start ? 8'd0 : wl_ptr;
  reg        bad_code;
  always @(*) begin
    bad_code = 1'b0;
    if (!BASELINE)
      for (n = 0; n < WPB; n = n + 1)
        if ((lidx * WPB + n < NW) && (uio_in[n*WB+:WB] == 2'b10)) bad_code = 1'b1;
  end

  // ------------------------------------------------------- sequential core
  task automatic raise(input [3:0] code);
    begin
      err      <= 1'b1;
      err_code <= code;
    end
  endtask

  always @(posedge clk) begin
    if (!rst_n) begin
      for (sn = 0; sn < NW; sn = sn + 1) begin
        w[sn]   <= {WB{1'b0}};
        opr[sn] <= {OPW{1'b0}};
        msk[sn] <= 1'b0;
      end
      for (sn = 0; sn < NTAPS; sn = sn + 1) begin
        win[sn] <= 8'd0;
        nzw[sn] <= 1'b0;
      end
      for (sn = 0; sn < NBUF; sn = sn + 1) rbuf[sn] <= {ACCW{1'b0}};
      wl_ptr <= 8'd0; w_loaded <= 1'b0;
      cfg_shift <= 4'd0; cfg_out8 <= 1'b0; cfg_acc <= 1'b0; cfg_zskip <= 1'b1;
      pend <= 1'b0; wi <= {DW{1'b0}}; waddr <= {DW{1'b0}};
      rd_mode <= 1'b0; diag_mode <= 1'b0;
      rb <= 3'd0; rc <= 2'd0; rs <= {DW{1'b0}};
      err <= 1'b0; sat <= 1'b0; err_code <= 4'd0;
    end else begin
      // ---- stage 2: commit the tap sums of the previous EXEC
      pend <= 1'b0;
      if (pend) begin
        for (sc = 0; sc < NCH; sc = sc + 1) begin
          // zero-window skip: nothing to add when accumulating
          if (!(cfg_acc && !anym[sc])) rbuf[waddr*NCH+sc] <= totq[sc];
          if (tsat[sc]) sat <= 1'b1;
        end
      end

      // ---- stage 1: host command
      if (valid) begin
        if (rd_mode && !(op == OP_READ || op == OP_DIAG)) begin
          rd_mode <= 1'b0;
          // a host write while the ASIC still drives uio is a bus contention
          if (op == OP_CONFIG || op == OP_LOADW || op == OP_EXEC) raise(E_CONT);
        end
        case (op)
          OP_NOP: ;
          OP_CONFIG: if (!rd_mode) begin
            cfg_shift <= uio_in[3:0];
            cfg_out8  <= uio_in[4];
            cfg_acc   <= uio_in[5];
            cfg_zskip <= uio_in[6];
          end
          OP_LOADW: if (!rd_mode) begin
            if (lidx >= NWBYTES) begin
              raise(E_WOVF);
            end else begin
              for (sn = 0; sn < NW; sn = sn + 1)
                if (sn / WPB == lidx) w[sn] <= uio_in[(sn%WPB)*WB+:WB];
              if (bad_code) raise(E_WCODE);
              wl_ptr <= lidx + 8'd1;
              if (last) begin
                w_loaded <= (lidx + 1 == NWBYTES);
                if (lidx + 1 != NWBYTES) raise(E_WSHORT);
              end else begin
                w_loaded <= 1'b0;
              end
            end
          end
          OP_EXEC: if (!rd_mode) begin
            if (!w_loaded)                       raise(E_NOW);
            else if (prec == 2'd3)               raise(E_PREC);
            else if (!start && wi >= DEPTH)      raise(E_FULL);
            else begin
              for (sk = 0; sk < NTAPS; sk = sk + 1) begin
                win[sk] <= nwin[sk];
                nzw[sk] <= nnz[sk];
              end
              for (sc = 0; sc < NCH; sc = sc + 1)
                for (sk = 0; sk < NTAPS; sk = sk + 1) begin
                  if (BASELINE) begin
                    opr[sc*NTAPS+sk] <= $signed(w[sc*NTAPS+sk]) * $signed(nwin[sk]);
                    msk[sc*NTAPS+sk] <= 1'b1;
                  end else begin
                    // ternary decode: 01 = +1, 11 = -1, 00 / 10 = 0 (skip)
                    if (w[sc*NTAPS+sk][0] && (nnz[sk] || !cfg_zskip))
                      opr[sc*NTAPS+sk] <= w[sc*NTAPS+sk][1] ? -$signed({nwin[sk][7], nwin[sk]})
                                                        :  $signed({nwin[sk][7], nwin[sk]});
                    msk[sc*NTAPS+sk] <= w[sc*NTAPS+sk][0] && (nnz[sk] || !cfg_zskip);
                  end
                end
              pend  <= 1'b1;
              waddr <= start ? {DW{1'b0}} : wi;
              wi    <= start ? {{(DW-1){1'b0}}, 1'b1} : wi + 1'b1;
            end
          end
          OP_READ: begin
            if (start) begin
              rd_mode <= 1'b1; diag_mode <= 1'b0;
              rb <= 3'd0; rc <= 2'd0; rs <= {DW{1'b0}};
              if (wi == 0) raise(E_RDEND);
            end else if (!rd_mode || diag_mode) begin
              raise(E_PROTO);
            end else if (at_last || wi == 0) begin
              raise(E_RDEND);
            end else if (rb == nbo - 1'b1) begin
              rb <= 3'd0;
              if (rc == NCH - 1) begin
                rc <= 2'd0;
                rs <= rs + 1'b1;
              end else begin
                rc <= rc + 1'b1;
              end
            end else begin
              rb <= rb + 3'd1;
            end
          end
          OP_CLEAR: if (last) begin
            // REWIND: restart the result buffer, keep window, results and flags,
            // so one long stream can be processed in DEPTH-sized pieces
            wi <= {DW{1'b0}};
            rd_mode <= 1'b0;
          end else begin
            for (sn = 0; sn < NBUF; sn = sn + 1) rbuf[sn] <= {ACCW{1'b0}};
            for (sn = 0; sn < NTAPS; sn = sn + 1) begin
              win[sn] <= 8'd0;
              nzw[sn] <= 1'b0;
            end
            for (sn = 0; sn < NW; sn = sn + 1) msk[sn] <= 1'b0;
            wi <= {DW{1'b0}}; pend <= 1'b0;
            rd_mode <= 1'b0; err <= 1'b0; sat <= 1'b0; err_code <= 4'd0;
          end
          OP_DIAG: begin
            if (start) begin
              rd_mode <= 1'b1; diag_mode <= 1'b1; rb <= 3'd0;
            end else if (!rd_mode || !diag_mode) begin
              raise(E_PROTO);
            end else begin
              rb <= rb + 3'd1;
            end
          end
          default: raise(E_OPC);
        endcase
      end
    end
  end

endmodule

"""Golden reference model for the ternary CNN convolution processor.

Bit-exact with src/tconv_core.v at the transaction level. Used by the cocotb
testbench (test/test.py) and by the accuracy / area experiments.
"""
from dataclasses import dataclass, field

# opcodes (ui_in[7:5])
NOP, CONFIG, LOADW, EXEC, READ, CLEAR, DIAG = range(7)
# error codes
E_OPC, E_PREC, E_NOW, E_FULL, E_WOVF, E_WCODE, E_WSHORT, E_CONT, E_RDEND, E_PROTO = range(1, 11)

TERN_CODE = {0: 0b00, 1: 0b01, -1: 0b11}


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def sat(v, bits):
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return min(max(v, lo), hi), (v < lo or v > hi)


def ext_act(byte, prec):
    """Activation byte -> signed value for precision mode 0 (INT8), 1 (INT4), 2 (INT2)."""
    return {0: sext(byte, 8), 1: sext(byte, 4), 2: sext(byte, 2)}[prec]


def pack_weights(weights, baseline=False):
    """weights: flat list ordered channel-major (c*NTAPS + k). Returns the LOADW bytes."""
    wb = 4 if baseline else 2
    per = 8 // wb
    out = []
    for i in range(0, len(weights), per):
        b = 0
        for j, w in enumerate(weights[i:i + per]):
            code = (w & 0xF) if baseline else TERN_CODE[w]
            b |= code << (j * wb)
        out.append(b)
    return out


def conv1d(x, w, start_fresh=True, history=None):
    """Causal 1-D convolution y[n] = sum_k w[k] * x[n-k] with zero history."""
    hist = list(history or [])
    ys = []
    for v in x:
        hist = [v] + hist
        ys.append(sum(wk * (hist[k] if k < len(hist) else 0) for k, wk in enumerate(w)))
    return ys


@dataclass
class TConvModel:
    ntaps: int = 7
    nch: int = 2
    accw: int = 16
    depth: int = 4
    baseline: bool = False
    # state
    w: list = field(default_factory=list)
    win: list = field(default_factory=list)
    buf: list = field(default_factory=list)
    wi: int = 0
    w_loaded: bool = False
    wl_ptr: int = 0
    shift: int = 0
    out8: bool = False
    acc: bool = False
    zskip: bool = True
    err: bool = False
    err_code: int = 0
    satf: bool = False

    def __post_init__(self):
        self.w = [0] * (self.nch * self.ntaps)
        self.win = [0] * self.ntaps
        self.buf = [[0] * self.nch for _ in range(self.depth)]

    @property
    def nb(self):
        return 1 if self.out8 else (self.accw + 7) // 8

    @property
    def nwbytes(self):
        per = 2 if self.baseline else 4
        return -(-(self.nch * self.ntaps) // per)

    def raise_(self, code):
        self.err, self.err_code = True, code

    # ---- commands -----------------------------------------------------
    def config(self, byte):
        self.shift = byte & 0xF
        self.out8 = bool(byte >> 4 & 1)
        self.acc = bool(byte >> 5 & 1)
        self.zskip = bool(byte >> 6 & 1)

    def loadw(self, byte, start, last):
        idx = 0 if start else self.wl_ptr
        if idx >= self.nwbytes:
            self.raise_(E_WOVF)
            return
        wb = 4 if self.baseline else 2
        per = 8 // wb
        bad = False
        for j in range(per):
            n = idx * per + j
            if n < len(self.w):
                code = (byte >> (j * wb)) & ((1 << wb) - 1)
                if self.baseline:
                    self.w[n] = sext(code, 4)
                else:
                    if code == 0b10:
                        bad = True
                    self.w[n] = {0b00: 0, 0b01: 1, 0b11: -1, 0b10: 0}[code]
        if bad:
            self.raise_(E_WCODE)
        self.wl_ptr = idx + 1
        if last:
            self.w_loaded = idx + 1 == self.nwbytes
            if not self.w_loaded:
                self.raise_(E_WSHORT)
        else:
            self.w_loaded = False

    def exec_(self, byte, prec, start):
        if not self.w_loaded:
            self.raise_(E_NOW)
            return
        if prec == 3:
            self.raise_(E_PREC)
            return
        if not start and self.wi >= self.depth:
            self.raise_(E_FULL)
            return
        a = ext_act(byte, prec)
        self.win = [a] + ([0] * (self.ntaps - 1) if start else self.win[:-1])
        slot = 0 if start else self.wi
        self.wi = 1 if start else self.wi + 1
        for c in range(self.nch):
            s = sum(self.w[c * self.ntaps + k] * self.win[k] for k in range(self.ntaps))
            tot = s + (self.buf[slot][c] if self.acc else 0)
            v, o = sat(tot, self.accw)
            self.buf[slot][c] = v
            self.satf |= o

    def clear(self):
        self.buf = [[0] * self.nch for _ in range(self.depth)]
        self.win = [0] * self.ntaps
        self.wi = 0
        self.err, self.err_code, self.satf = False, 0, False

    def rewind(self):
        self.wi = 0

    def read_bytes(self):
        """Every byte a full READ burst returns, in order."""
        out = []
        for s in range(self.wi):
            for c in range(self.nch):
                v = self.buf[s][c]
                if self.out8:
                    out.append(sat(v >> self.shift, 8)[0] & 0xFF)
                else:
                    u = v & ((1 << (8 * self.nb)) - 1)
                    out += [(u >> (8 * b)) & 0xFF for b in range(self.nb)]
        return out

    def results(self):
        return [row[:] for row in self.buf[:self.wi]]

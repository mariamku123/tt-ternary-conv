# SPDX-License-Identifier: Apache-2.0
"""Self-checking cocotb testbench for the ternary CNN convolution processor.

Every transaction is applied to both the DUT pins and the Python golden model
(model/tconv_model.py); read-back bytes and status flags are compared.
Design parameters are discovered from the DIAG bytes, so the same tests run
for every point of the parameter sweep (make NTAPS=5 NCH=1 ACCW=12 ...).
"""
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))
import tconv_model as M  # noqa: E402

SEED = int(os.environ.get("SEED", "621"))


class Host:
    """Drives the Tiny Tapeout pins, mirrors every command in the golden model."""

    def __init__(self, dut):
        self.dut = dut
        self.rd_mode = False
        self.driving = False
        self.contention = 0
        self.m = None

    # ---------------------------------------------------------------- setup
    async def reset(self):
        d = self.dut
        d.ena.value = 1
        d.ui_in.value = 0
        d.uio_in.value = 0
        d.rst_n.value = 0
        await ClockCycles(d.clk, 5)
        d.rst_n.value = 1
        await FallingEdge(d.clk)
        self.rd_mode = False
        self.driving = False
        if self.m is not None:
            self.m = M.TConvModel(self.m.ntaps, self.m.nch, self.m.accw, self.m.depth, self.m.baseline)

    async def bus_monitor(self):
        """Flag any cycle in which both the host and the ASIC drive uio."""
        while True:
            await RisingEdge(self.dut.clk)
            if self.driving and int(self.dut.uio_oe.value) != 0:
                self.contention += 1

    # ------------------------------------------------------------ low level
    async def raw(self, op, data=0, prec=0, start=0, last=0, drive=True):
        """Present one packet for one clock; returns at the next falling edge."""
        d = self.dut
        d.ui_in.value = (op << 5) | (prec << 3) | (1 << 2) | (last << 1) | start
        self.driving = drive
        d.uio_in.value = data if drive else 0
        await FallingEdge(d.clk)
        d.ui_in.value = 0
        self.driving = False
        d.uio_in.value = 0

    async def idle(self, n=1):
        await ClockCycles(self.dut.clk, n, rising=False)

    # ------------------------------------------------------------- commands
    async def release(self):
        """Leave read mode and wait for the turnaround before driving uio."""
        if self.rd_mode:
            await self.raw(M.NOP, drive=False)
            self.rd_mode = False
        while int(self.dut.uio_oe.value) != 0:
            await self.idle()

    async def config(self, shift=0, out8=0, acc=0, zskip=1):
        await self.release()
        b = shift | out8 << 4 | acc << 5 | zskip << 6
        await self.raw(M.CONFIG, b)
        self.m.config(b)

    async def loadw(self, weights):
        await self.release()
        bs = M.pack_weights(weights, self.m.baseline)
        for i, b in enumerate(bs):
            await self.raw(M.LOADW, b, start=int(i == 0), last=int(i == len(bs) - 1))
            self.m.loadw(b, i == 0, i == len(bs) - 1)

    async def execute(self, acts, prec=0, start=True):
        await self.release()
        for i, a in enumerate(acts):
            s = int(start and i == 0)
            await self.raw(M.EXEC, a & 0xFF, prec=prec, start=s)
            self.m.exec_(a & 0xFF, prec, bool(s))
        await self.idle()  # let stage 2 commit

    async def read(self):
        """Full READ burst; returns the bytes seen on uio_out."""
        await self.raw(M.READ, start=1, drive=False)
        self.rd_mode = True
        if self.m.wi == 0:
            self.m.raise_(M.E_RDEND)
        n = len(self.m.read_bytes())
        out = []
        for i in range(n):
            assert int(self.dut.uio_oe.value) == 0xFF, "uio_oe must be 0xFF in read mode"
            out.append(int(self.dut.uio_out.value))
            if i != n - 1:
                await self.raw(M.READ, drive=False)
        if n:
            assert self.status()["done"], "done must flag the last read byte"
        return out

    async def diag(self):
        await self.release()
        await self.raw(M.DIAG, start=1, drive=False)
        self.rd_mode = True
        out = []
        for i in range(8):
            out.append(int(self.dut.uio_out.value))
            await self.raw(M.DIAG, drive=False)
        await self.release()
        return out

    async def rewind(self):
        await self.release()
        await self.raw(M.CLEAR, last=1, drive=False)
        self.m.rewind()

    async def clear(self):
        await self.raw(M.CLEAR, drive=False)
        self.rd_mode = False
        self.m.clear()

    # --------------------------------------------------------------- checks
    def status(self):
        v = int(self.dut.uo_out.value)
        names = ["ready", "busy", "done", "error", "rd_mode", "sat", "w_loaded", "empty"]
        return {n: (v >> i) & 1 for i, n in enumerate(names)}

    def check_status(self):
        s, m = self.status(), self.m
        full = m.wi == m.depth
        exp = {
            "error": int(m.err), "sat": int(m.satf), "w_loaded": int(m.w_loaded),
            "empty": int(m.wi == 0), "rd_mode": int(self.rd_mode),
            "ready": int(m.w_loaded and not full and not self.rd_mode),
        }
        if not self.rd_mode:
            exp["done"] = int(full)
        for k, v in exp.items():
            assert s[k] == v, f"status {k}: dut={s[k]} model={v} (all={s})"

    async def check_results(self):
        self.check_status()
        got = await self.read()
        exp = self.m.read_bytes()
        assert got == exp, f"read-back mismatch\n dut  ={got}\n model={exp}\n results={self.m.results()}"
        await self.release()
        return got

    async def err_code(self):
        return (await self.diag())[4] & 0xF


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())  # 25 MHz
    h = Host(dut)
    await h.reset()
    d = await h.diag()
    assert d[0] == 0xA9, f"bad DIAG magic {d[0]:#x}"
    ntaps, nch = d[1] & 0xF, d[1] >> 4
    h.m = M.TConvModel(ntaps=ntaps, nch=nch, accw=d[2], depth=d[3], baseline=bool(d[4] >> 7))
    cocotb.start_soon(h.bus_monitor())
    dut._log.info(f"DUT: taps={ntaps} ch={nch} accw={d[2]} depth={d[3]} baseline={h.m.baseline}")
    return h


def rand_weights(h, rng, choices=None):
    if h.m.baseline:
        return [rng.randint(-8, 7) for _ in range(h.m.nch * h.m.ntaps)]
    return [rng.choice(choices or [-1, 0, 1]) for _ in range(h.m.nch * h.m.ntaps)]


def rand_acts(h, rng, n=None, zero_p=0.3):
    n = n or h.m.depth
    return [0 if rng.random() < zero_p else rng.randint(-128, 127) for _ in range(n)]


# ======================================================================= tests
@cocotb.test()
async def test_reset_and_idle(dut):
    """Outputs are deterministic after reset and while idle."""
    h = await setup(dut)
    for _ in range(5):
        assert int(dut.uio_oe.value) == 0
        assert int(dut.uio_out.value) == 0
        assert int(dut.uo_out.value) == 0b1000_0000  # only 'empty'
        await h.idle()
    h.check_status()


@cocotb.test()
async def test_all_ternary_codes(dut):
    """Every weight code (+1, 0, -1) on every tap, checked against the model."""
    h = await setup(dut)
    rng = random.Random(SEED)
    vals = [-8, 7, 0, 1] if h.m.baseline else [-1, 0, 1]
    for v in vals:  # each code on all taps
        await h.loadw([v] * (h.m.nch * h.m.ntaps))
        await h.execute(rand_acts(h, rng, zero_p=0))
        await h.check_results()
    for _ in range(10):  # mixed codes
        await h.loadw(rand_weights(h, rng))
        await h.execute(rand_acts(h, rng))
        await h.check_results()
    assert not h.m.err


@cocotb.test()
async def test_negative_results(dut):
    """-1 weights on positive data and +1 weights on negative data give negative outputs."""
    h = await setup(dut)
    neg = -8 if h.m.baseline else -1
    await h.loadw([neg] * (h.m.nch * h.m.ntaps))
    await h.execute([127] * h.m.depth)
    await h.check_results()
    assert all(v < 0 for row in h.m.results() for v in row)
    await h.loadw([1] * (h.m.nch * h.m.ntaps))
    await h.execute([-128] * h.m.depth)
    await h.check_results()
    assert all(v < 0 for row in h.m.results() for v in row)


@cocotb.test()
async def test_zero_activation_windows(dut):
    """All-zero windows give zero, with and without zero skipping."""
    h = await setup(dut)
    rng = random.Random(SEED + 1)
    await h.loadw(rand_weights(h, rng))
    for zs in (1, 0):
        await h.config(zskip=zs)
        await h.execute([0] * h.m.depth)
        await h.check_results()
        assert all(v == 0 for row in h.m.results() for v in row)


@cocotb.test()
async def test_zero_skip_is_exact(dut):
    """Zero skipping changes switching activity only, never the results."""
    h = await setup(dut)
    rng = random.Random(SEED + 2)
    for _ in range(10):
        w = rand_weights(h, rng)
        x = rand_acts(h, rng, zero_p=0.6)
        res = []
        for zs in (1, 0):
            await h.config(zskip=zs)
            await h.loadw(w)
            await h.execute(x)
            res.append(await h.check_results())
        assert res[0] == res[1]


@cocotb.test()
async def test_filter_reload(dut):
    """New weights take effect on the next sample, and partial loads are rejected."""
    h = await setup(dut)
    rng = random.Random(SEED + 3)
    for _ in range(4):
        await h.loadw(rand_weights(h, rng))
        await h.execute(rand_acts(h, rng))
        await h.check_results()
    # reload in the middle of a stream: window history must survive the reload
    await h.loadw(rand_weights(h, rng))
    await h.execute(rand_acts(h, rng, n=2))
    await h.loadw(rand_weights(h, rng))
    await h.execute(rand_acts(h, rng, n=h.m.depth - 2), start=False)
    await h.check_results()


@cocotb.test()
async def test_boundary_conditions(dut):
    """start = zero padding; a long stream continues across REWIND; buffer-full is an error."""
    h = await setup(dut)
    rng = random.Random(SEED + 4)
    await h.loadw(rand_weights(h, rng, [1, -1]))
    nblk = 3
    x = rand_acts(h, rng, n=nblk * h.m.depth, zero_p=0.2)
    got = []
    for i in range(nblk):  # one long stream, read back in DEPTH-sized pieces
        if i:
            await h.rewind()
        await h.execute(x[i * h.m.depth:(i + 1) * h.m.depth], start=(i == 0))
        await h.check_results()
        got += h.m.results()
    for c in range(h.m.nch):  # independent check against a plain causal convolution
        w = h.m.w[c * h.m.ntaps:(c + 1) * h.m.ntaps]
        ref = [M.sat(v, h.m.accw)[0] for v in M.conv1d(x, w)]
        assert [r[c] for r in got] == ref
    # buffer full -> E_FULL, sample dropped, earlier results intact
    await h.raw(M.EXEC, 5)
    h.m.exec_(5, 0, False)
    assert h.m.err_code == M.E_FULL
    await h.check_results()
    assert await h.err_code() == M.E_FULL
    # a new start zero-pads: first output only sees tap 0
    await h.clear()
    await h.execute([100] + [0] * (h.m.depth - 1))
    await h.check_results()
    for c in range(h.m.nch):
        assert h.m.results()[0][c] == h.m.w[c * h.m.ntaps] * 100


@cocotb.test()
async def test_saturation(dut):
    """Accumulation clamps at the ACCW limits and sets the sticky sat flag."""
    h = await setup(dut)
    lim = 1 << (h.m.accw - 1)
    for sign in (1, -1):
        await h.clear()
        await h.config()
        w = 7 if h.m.baseline else 1
        await h.loadw([w] * (h.m.nch * h.m.ntaps))
        a = 127 if sign > 0 else -128
        # prime the window so every tap is busy, then accumulate in place
        await h.execute([a] * h.m.depth)
        await h.config(acc=1)
        per = h.m.ntaps * abs(a) * abs(w)
        for _ in range(lim // per + 3):
            await h.rewind()
            await h.execute([a] * h.m.depth, start=False)
        assert h.m.satf
        await h.check_results()
        vals = [v for row in h.m.results() for v in row]
        assert all(v == (lim - 1 if sign > 0 else -lim) for v in vals), vals
        assert h.status()["sat"] == 1
        # 8-bit shifted output mode also saturates
        await h.config(acc=1, out8=1, shift=2)
        await h.check_results()


@cocotb.test()
async def test_precision_modes(dut):
    """INT8, INT4 and INT2 activations; precision code 3 is rejected."""
    h = await setup(dut)
    rng = random.Random(SEED + 5)
    await h.loadw(rand_weights(h, rng))
    for prec in (0, 1, 2):
        await h.execute([rng.randint(0, 255) for _ in range(h.m.depth)], prec=prec)
        await h.check_results()
    await h.raw(M.EXEC, 1, prec=3, start=1)
    h.m.exec_(1, 3, True)
    h.check_status()
    assert await h.err_code() == M.E_PREC


@cocotb.test()
async def test_output_modes(dut):
    """Full-width LSB-first read-back and shifted 8-bit read-back."""
    h = await setup(dut)
    rng = random.Random(SEED + 6)
    await h.loadw(rand_weights(h, rng))
    await h.execute(rand_acts(h, rng, zero_p=0))
    for shift in range(0, 16, 3):
        await h.config(out8=1, shift=shift)
        await h.check_results()
    await h.config()
    await h.check_results()


@cocotb.test()
async def test_malformed_commands(dut):
    """Each protocol violation raises error with the right code and is ignored."""
    h = await setup(dut)
    rng = random.Random(SEED + 7)

    async def expect(code):
        assert h.m.err_code == code, (h.m.err_code, code)
        h.check_status()
        assert await h.err_code() == code
        await h.clear()

    # EXEC before any weights
    await h.raw(M.EXEC, 3, start=1); h.m.exec_(3, 0, True)
    await expect(M.E_NOW)
    # reserved opcode 7
    await h.raw(7, 0); h.m.raise_(M.E_OPC)
    await expect(M.E_OPC)
    # short weight load
    await h.raw(M.LOADW, 0x55, start=1, last=1); h.m.loadw(0x55, True, True)
    exp = M.E_WSHORT if h.m.nwbytes > 1 else None
    if exp:
        await expect(exp)
    # weight overflow
    await h.loadw(rand_weights(h, rng))
    await h.raw(M.LOADW, 0); h.m.loadw(0, False, False)
    await expect(M.E_WOVF)
    # illegal ternary code 10
    if not h.m.baseline:
        bs = M.pack_weights([0] * (h.m.nch * h.m.ntaps))
        bs[0] |= 0b10
        for i, b in enumerate(bs):
            await h.raw(M.LOADW, b, start=int(i == 0), last=int(i == len(bs) - 1))
            h.m.loadw(b, i == 0, i == len(bs) - 1)
        await expect(M.E_WCODE)
    # READ continuation without READ start
    await h.loadw(rand_weights(h, rng))
    await h.raw(M.READ, drive=False); h.m.raise_(M.E_PROTO)
    await expect(M.E_PROTO)
    # read past the end
    await h.execute(rand_acts(h, rng, n=1))
    await h.read()
    await h.raw(M.READ, drive=False); h.m.raise_(M.E_RDEND)
    await h.release()
    await expect(M.E_RDEND)
    # bus contention: host writes while the ASIC still drives uio
    await h.execute(rand_acts(h, rng, n=1))
    await h.raw(M.READ, start=1, drive=False)
    h.rd_mode = True
    await h.raw(M.CONFIG, 0x7F, drive=False)  # data not driven: the DUT must ignore it
    h.rd_mode = False
    h.m.raise_(M.E_CONT)
    await expect(M.E_CONT)
    assert h.m.shift == 0


@cocotb.test()
async def test_reset_under_load(dut):
    """Reset in the middle of a burst returns to the idle state."""
    h = await setup(dut)
    rng = random.Random(SEED + 8)
    await h.loadw(rand_weights(h, rng))
    await h.execute(rand_acts(h, rng, n=2))
    await h.raw(M.READ, start=1, drive=False)
    assert int(dut.uio_oe.value) == 0xFF
    await h.reset()
    assert int(dut.uio_oe.value) == 0
    assert int(dut.uo_out.value) == 0b1000_0000
    h.check_status()


@cocotb.test()
async def test_random_transactions(dut):
    """Seeded random mix of all legal commands, scoreboarded against the model."""
    h = await setup(dut)
    rng = random.Random(SEED + 9)
    n_tx = int(os.environ.get("N_TX", "300"))
    await h.loadw(rand_weights(h, rng))
    for t in range(n_tx):
        r = rng.random()
        if r < 0.15:
            await h.loadw(rand_weights(h, rng))
        elif r < 0.25:
            await h.config(shift=rng.randint(0, 15), out8=rng.randint(0, 1),
                           acc=int(rng.random() < 0.2), zskip=rng.randint(0, 1))
        elif r < 0.75:
            free = h.m.depth - h.m.wi
            start = rng.random() < 0.5 or free == 0
            n = rng.randint(1, h.m.depth if start else free)
            await h.execute(rand_acts(h, rng, n=n, zero_p=rng.random()), prec=rng.choice([0, 0, 1, 2]),
                            start=start)
        elif r < 0.95:
            if h.m.wi:
                await h.check_results()
            elif h.m.wi == 0 and rng.random() < 0.5:
                await h.rewind()
        else:
            await h.clear()
        h.check_status()
        assert not h.m.err, f"tx {t}: unexpected error {h.m.err_code}"
    assert h.contention == 0, f"{h.contention} cycles of bus contention"

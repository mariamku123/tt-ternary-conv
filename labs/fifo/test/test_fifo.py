# Lab 3 self-checking FIFO tests: reference queue scoreboard, directed + random traffic.
import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

DEPTH = 4


class Fifo:
    def __init__(self, dut):
        self.dut, self.q, self.err = dut, deque(), False
        self.cov = set()

    async def reset(self):
        d = self.dut
        d.ena.value, d.ui_in.value, d.uio_in.value, d.rst_n.value = 1, 0, 0, 0
        await ClockCycles(d.clk, 3)
        d.rst_n.value = 1
        await FallingEdge(d.clk)
        self.q.clear()
        self.err = False

    async def step(self, write=0, read=0, clear=0, din=0):
        d = self.dut
        before = len(self.q)
        d.ui_in.value = din
        d.uio_in.value = (write << 3) | (read << 4) | (clear << 5)
        await FallingEdge(d.clk)
        d.uio_in.value = 0
        # reference model
        do_read = read and before > 0
        do_write = write and (before < DEPTH or do_read)
        if do_read:
            self.q.popleft()
        if do_write:
            self.q.append(din)
        if clear:
            self.err = False
        elif (write and not do_write) or (read and before == 0):
            self.err = True
        after = len(self.q)
        if before == 0 and after > 0: self.cov.add("empty->nonempty")
        if before == DEPTH and after < DEPTH: self.cov.add("full->nonfull")
        if write and before == DEPTH: self.cov.add("overflow request")
        if read and before == 0: self.cov.add("underflow request")
        if write and read: self.cov.add("simultaneous")
        self.check()

    def check(self):
        d, n = self.dut, len(self.q)
        st = int(d.uio_out.value)
        exp = {0: n == DEPTH, 1: n == 0, 2: n > 0, 6: self.err, 7: n >= DEPTH - 1}
        for bit, v in exp.items():
            assert (st >> bit) & 1 == int(v), f"uio_out[{bit}] wrong: {st:08b}, count={n}, err={self.err}"
        assert int(d.uio_oe.value) == 0xC7, "uio_oe must stay 0xC7"
        assert int(d.uo_out.value) == (self.q[0] if n else 0), "DOUT != head of reference queue"


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    f = Fifo(dut)
    await f.reset()
    f.check()
    return f


@cocotb.test()
async def test_walking_patterns_and_bit7(dut):
    """Walking one / zero, 0x80 and 0xFF pass through unchanged."""
    f = await setup(dut)
    pats = [1 << i for i in range(8)] + [0xFF ^ (1 << i) for i in range(8)] + [0x80, 0xFF, 0x00]
    for p in pats:
        await f.step(write=1, din=p)
        await f.step(read=1)


@cocotb.test()
async def test_full_to_empty_sweep(dut):
    """Fill to 4 (almost_full at 3 and 4), overflow, then drain to 0, underflow, clear."""
    f = await setup(dut)
    for i in range(DEPTH):
        await f.step(write=1, din=0xA0 + i)
    await f.step(write=1, din=0xEE)          # overflow, dropped
    await f.step(write=1, read=1, din=0x5A)  # simultaneous on full: allowed
    for _ in range(DEPTH):
        await f.step(read=1)
    await f.step(read=1)                     # underflow
    await f.step(clear=1)
    assert not f.err


@cocotb.test()
async def test_pointer_wrap(dut):
    """Many enqueue/dequeue rounds wrap the pointers several times."""
    f = await setup(dut)
    for r in range(10):
        for i in range(3):
            await f.step(write=1, din=(r * 3 + i) & 0xFF)
        for i in range(3):
            await f.step(read=1)


@cocotb.test()
async def test_reset_under_load(dut):
    """Reset with data inside empties the FIFO and clears error."""
    f = await setup(dut)
    for i in range(3):
        await f.step(write=1, din=i + 1)
    await f.step(read=1)
    await f.reset()
    f.check()


@cocotb.test()
async def test_random_traffic(dut):
    """At least 100 legal random transactions plus random illegal ones, with coverage."""
    f = await setup(dut)
    rng = random.Random(int(os.environ.get("SEED", "621")))
    for _ in range(400):
        await f.step(write=int(rng.random() < 0.55), read=int(rng.random() < 0.5),
                     clear=int(rng.random() < 0.05), din=rng.randint(0, 255))
    need = {"empty->nonempty", "full->nonfull", "overflow request", "underflow request", "simultaneous"}
    assert need <= f.cov, f"missing coverage: {need - f.cov}"
    dut._log.info(f"coverage hit: {sorted(f.cov)}")

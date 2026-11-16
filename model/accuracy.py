"""Approximation error of ternary (BitNet b1.58 absmean) vs INT4 weights.

For each filter length (3/5/7 taps) the script draws random float filters and
INT8 input signals, quantises the weights two ways, runs the bit-exact integer
convolution that the chip computes, rescales, and reports the SQNR against the
float convolution:

  ternary: gamma = mean(|w|); w_t = clip(round(w / gamma), -1, 1); y ~ gamma * conv(x, w_t)
  INT4:    s = max(|w|) / 7;  w_q = clip(round(w / s), -8, 7);    y ~ s * conv(x, w_q)

Usage: python3 model/accuracy.py [--trials 2000] [--seed 621] [--csv out.csv]
"""
import argparse
import csv

import numpy as np


def ternary_absmean(w):
    g = np.mean(np.abs(w)) + 1e-12
    return np.clip(np.round(w / g), -1, 1).astype(int), g


def int4_absmax(w):
    s = np.max(np.abs(w)) / 7 + 1e-12
    return np.clip(np.round(w / s), -8, 7).astype(int), s


def conv(x, w):
    return np.convolve(x, w)[: len(x)]  # causal, zero history: y[n] = sum_k w[k] x[n-k]


def sqnr_db(ref, approx):
    return 10 * np.log10(np.sum(ref ** 2) / max(np.sum((ref - approx) ** 2), 1e-30))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=621)
    ap.add_argument("--len", type=int, default=64)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    rows = []
    for taps in (3, 5, 7):
        res = {"ternary": [], "int4": []}
        zero_frac = []
        for _ in range(a.trials):
            w = rng.standard_normal(taps)                       # float filter
            x = np.clip(np.round(rng.standard_normal(a.len) * 40), -128, 127)
            x[rng.random(a.len) < 0.3] = 0                      # ReLU-like sparsity
            ref = conv(x, w)
            wt, g = ternary_absmean(w)
            wq, s = int4_absmax(w)
            res["ternary"].append(sqnr_db(ref, g * conv(x, wt)))
            res["int4"].append(sqnr_db(ref, s * conv(x, wq)))
            zero_frac.append(np.mean(wt == 0))
        for k, v in res.items():
            rows.append({"taps": taps, "weights": k, "sqnr_db_median": round(float(np.median(v)), 2),
                         "sqnr_db_p10": round(float(np.percentile(v, 10)), 2),
                         "zero_weight_frac": round(float(np.mean(zero_frac)), 3) if k == "ternary" else ""})
    print(f"{'taps':>4} {'weights':>8} {'SQNR med':>9} {'SQNR p10':>9} {'zero w':>7}")
    for r in rows:
        print(f"{r['taps']:>4} {r['weights']:>8} {r['sqnr_db_median']:>9} {r['sqnr_db_p10']:>9} {r['zero_weight_frac']!s:>7}")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=rows[0].keys())
            wr.writeheader()
            wr.writerows(rows)


if __name__ == "__main__":
    main()

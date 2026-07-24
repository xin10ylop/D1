"""Verify universe_old labels against Binance 1m klines (resolution source).

S_T = close of the 1m candle opening at end_dt (candle labeled 12:00 ET),
same convention as evaluate.load_panel (T_pm = end+60; candle open T_pm-60).
"""
import sys
import pandas as pd

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA

ME = DATA.parent / "results/improve/mechanism-extend"


def load_bin(sym):
    parts = []
    for suf in ["_old", ""]:
        f = DATA / f"binance/{sym}_1m{suf}.parquet"
        if f.exists():
            parts.append(pd.read_parquet(f, columns=["open_time_us", "close"]))
    kl = pd.concat(parts).drop_duplicates("open_time_us").sort_values("open_time_us")
    return kl


if __name__ == "__main__":
    u = pd.read_parquet(ME / "universe_old.parquet")
    epoch = pd.Timestamp(0, tz="UTC")
    u["T_open_s"] = (u["end_dt"] - epoch) // pd.Timedelta(seconds=1)
    out = []
    for asset, sym in [("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")]:
        kl = load_bin(sym)
        cmap = dict(zip((kl["open_time_us"] // 1_000_000).astype(int), kl["close"]))
        g = u[u.asset == asset].copy()
        g["S_T"] = g["T_open_s"].map(cmap)
        out.append(g)
    v = pd.concat(out)
    v["pred"] = (v["S_T"] > v["strike"]).astype(int)
    v["match"] = (v["pred"] == v["label"])
    print("with S_T:", v.S_T.notna().sum(), "/", len(v))
    print("label match rate:", v[v.S_T.notna()].match.mean())
    bad = v[v.S_T.notna() & ~v.match]
    print("mismatches:", len(bad))
    if len(bad):
        bad["absdist"] = (bad.S_T / bad.strike - 1).abs()
        print(bad[["slug", "end_dt", "strike", "S_T", "label", "pred", "absdist"]]
              .sort_values("absdist").head(25).to_string())
    v.to_parquet(ME / "universe_verified.parquet", index=False)

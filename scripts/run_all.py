"""Full-sample orchestration: rebuild panels -> run all battery families -> report tables.

Usage: python3 scripts/run_all.py [--panels-only]
"""
import sys, subprocess, datetime as dt
import pandas as pd
import numpy as np
sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA, ROOT


def rebuild_panels():
    for a in ["BTC", "ETH", "SOL", "XRP"]:
        print(f"[{dt.datetime.now()}] building panel {a}", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/build_panel2.py"), a], check=True,
                       cwd=str(ROOT / "scripts"))


def run_battery():
    from battery import (prep, battery_thresholds, battery_maker, battery_calendar,
                         battery_weekend_hours)
    panels = [prep(a) for a in ["BTC", "ETH", "SOL", "XRP"]]
    res = ROOT / "results"
    out = {}
    for sample in ["train", "test"]:
        thr = battery_thresholds(panels, sample)
        thr.to_csv(res / f"battery_thresholds_{sample}.csv", index=False)
        out[f"thr_{sample}"] = thr
        mk = battery_maker(panels, sample)
        mk.to_csv(res / f"battery_maker_{sample}.csv", index=False)
        out[f"mk_{sample}"] = mk
        cal_d, cal_s = battery_calendar(panels, sample)
        if len(cal_d):
            cal_d.to_csv(res / f"battery_calendar_{sample}.csv", index=False)
        out[f"cal_{sample}"] = cal_s
        print(f"[{sample}] thresholds:{len(thr)} maker:{len(mk)} calendar:{cal_s}", flush=True)
    return out


if __name__ == "__main__":
    if "--battery-only" not in sys.argv:
        rebuild_panels()
    if "--panels-only" not in sys.argv:
        out = run_battery()
        pd.set_option("display.width", 250)
        for k in ["thr_train", "thr_test", "mk_train", "mk_test"]:
            df = out[k]
            if len(df):
                print(f"\n==== {k} (top by t) ====")
                print(df.sort_values("t", ascending=False).head(12).to_string())

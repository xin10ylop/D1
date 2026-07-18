import os, json, time, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass  # dotenv is only needed for data-fetch scripts, not the paper/live bot

DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# Only the historical data-download scripts need this; the bot runs on public
# APIs and does not. Absent key -> None (crash only if a fetch script uses it).
TLX_KEY = os.environ.get("TELONEX_API_KEY")

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
ASSET_WORD = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}


def rpc(host, method, params, timeout=90, retries=5):
    import urllib.request, urllib.error, random
    url = f"https://{host}/api/v2/{method}?" + "&".join(f"{k}={v}" for k, v in params.items())
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
                out = json.loads(r.read())
                if "result" in out:
                    return out["result"]
                raise RuntimeError(str(out)[:200])
        except Exception as e:
            if a == retries - 1:
                raise
            time.sleep(min(2 ** a, 20) + random.random())

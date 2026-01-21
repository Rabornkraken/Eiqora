
import requests
import json

API_KEY = "q8EtfpvwhR6Jc1AhWu9vmh4MluzmtcHE"
SYMBOL = "AAPL"

ENDPOINTS = [
    ("Daily (v3)", f"https://financialmodelingprep.com/api/v3/historical-price-full/{SYMBOL}?apikey={API_KEY}"),
    ("Hourly (v3)", f"https://financialmodelingprep.com/api/v3/historical-chart/1hour/{SYMBOL}?apikey={API_KEY}"),
    ("4 Hour (v3)", f"https://financialmodelingprep.com/api/v3/historical-chart/4hour/{SYMBOL}?apikey={API_KEY}"),
    ("Hourly (v4)", f"https://financialmodelingprep.com/api/v4/historical-price/1hour/{SYMBOL}?apikey={API_KEY}"),
    ("Profile (v3)", f"https://financialmodelingprep.com/api/v3/profile/{SYMBOL}?apikey={API_KEY}"),
]

def test_fmp():
    for name, url in ENDPOINTS:
        print(f"Testing {name}...")
        try:
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                # Daily returns {symbol: ..., historical: [...]}, others return list
                if isinstance(data, dict) and "historical" in data:
                    count = len(data["historical"])
                    print(f"  ✅ Success! {count} bars.")
                elif isinstance(data, list):
                    print(f"  ✅ Success! {len(data)} bars.")
                    if len(data) > 0 and "date" in data[0]:
                        # FMP returns newest first
                        first = data[-1]['date']
                        last = data[0]['date']
                        print(f"     Range: {first} -> {last}")
                else:
                    print(f"  ❓ Unknown format: {str(data)[:100]}")
            else:
                print(f"  ❌ Error {response.status_code}: {response.text[:100]}")
        except Exception as e:
            print(f"  ❌ Exception: {e}")
        print("-" * 20)

if __name__ == "__main__":
    test_fmp()

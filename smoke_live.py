import json

import httpx

BASE = "http://127.0.0.1:8123"

h = httpx.get(f"{BASE}/healthz", timeout=30).json()
print("healthz:", h)

r = httpx.post(f"{BASE}/demo/rex", timeout=120).json()
print(f"\nRex agent: {r['agent']}  caught_all: {r['caught_all']}")
for row in r["report"]:
    mark = "OK " if row["caught"] else "!!!"
    print(f"  [{mark}] {row['actual']:15s} <- {row['attack']}")
    if row["reasons"]:
        print(f"        {row['reasons'][0]}")

d = httpx.post(f"{BASE}/demo/drift", timeout=120).json()
print(f"\nwatchdog new_alerts: {len(d['new_alerts'])}")
for a in d["new_alerts"]:
    print(f"  {a['kind']:15s} {a['summary']}")

alerts = httpx.get(f"{BASE}/alerts", timeout=30).json()
if alerts:
    draft = httpx.post(f"{BASE}/alerts/{alerts[0]['id']}/dispute", timeout=60).json()
    print(f"\ndispute draft title: {draft['draft']['title']}")
    print(f"dispute drafted_by: {draft['draft']['drafted_by']}")
    print("first 160 chars:", draft["draft"]["body"][:160].replace("\n", " "))

chain = httpx.get(f"{BASE}/receipts/{r['agent']}", timeout=30).json()
print(f"\nreceipt chain for {r['agent']}: {chain['count']} receipts, "
      f"valid={chain['chain_valid']}")
print("\nLIVE SMOKE OK")

import httpx
import json

c = httpx.Client(timeout=30.0)

# Login
login = c.post(
    "http://localhost:8000/api/v1/auth/login",
    json={"email": "test@psx.com", "password": "testpass123"},
)
token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Get companies
r = c.get("http://localhost:8000/api/v1/companies?limit=10", headers=headers)
data = r.json()
print(f"Total companies: {data['total']}")
for item in data["items"]:
    print(f"  {item['ticker']}: {item['name']} ({item['sector']})")

# Get pipeline status
r2 = c.get("http://localhost:8000/api/v1/pipeline/status")
print(f"\nPipeline runs: {len(r2.json())}")
for run in r2.json():
    print(f"  {run['pipeline_name']}: {run['status']}")

# Test idempotent seed (should insert 0)
r3 = c.post("http://localhost:8000/api/v1/pipeline/seed", headers=headers)
print(f"\nIdempotent seed: {r3.json()}")

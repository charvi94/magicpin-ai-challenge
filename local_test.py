
import json
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE = "http://localhost:8080"
ROOT = Path(__file__).parent
EXP = ROOT / "expanded"

def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode())

def load_one(folder):
    files = list((EXP / folder).glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No JSON files found in expanded/{folder}")
    return json.loads(files[0].read_text(encoding="utf-8"))

now = datetime.now(timezone.utc).isoformat()

print("1) HEALTH")
print(get("/v1/healthz"))

print("\n2) METADATA")
print(get("/v1/metadata"))

# Load one category, merchant, customer and trigger.
category_file = next((EXP / "categories").glob("*.json"))
category = json.loads(category_file.read_text(encoding="utf-8"))

merchant = load_one("merchants")
customer = load_one("customers")
trigger = load_one("triggers")

print("\n3) LOADED SAMPLE")
print("category :", category.get("slug"))
print("merchant :", merchant.get("merchant_id"))
print("customer :", customer.get("customer_id"))
print("trigger  :", trigger.get("id"))

# Push category
print("\n4) PUSH CATEGORY")
print(post("/v1/context", {
    "scope": "category",
    "context_id": category.get("slug"),
    "version": 1,
    "payload": category,
    "delivered_at": now
}))

# Push merchant
print("\n5) PUSH MERCHANT")
print(post("/v1/context", {
    "scope": "merchant",
    "context_id": merchant["merchant_id"],
    "version": 1,
    "payload": merchant,
    "delivered_at": now
}))

# Push customer only if it belongs to this merchant.
if customer.get("merchant_id") == merchant.get("merchant_id"):
    print("\n6) PUSH CUSTOMER")
    print(post("/v1/context", {
        "scope": "customer",
        "context_id": customer["customer_id"],
        "version": 1,
        "payload": customer,
        "delivered_at": now
    }))
else:
    print("\n6) CUSTOMER belongs to another merchant; skipping customer context")

# Pick a trigger belonging to our sample merchant if possible.
trigger_files = list((EXP / "triggers").glob("*.json"))
matching = []
for f in trigger_files:
    t = json.loads(f.read_text(encoding="utf-8"))
    if t.get("merchant_id") == merchant.get("merchant_id"):
        matching.append(t)

if matching:
    trigger = matching[0]

print("\n7) PUSH TRIGGER")
print(post("/v1/context", {
    "scope": "trigger",
    "context_id": trigger["id"],
    "version": 1,
    "payload": trigger,
    "delivered_at": now
}))

print("\n8) TICK")
tick = post("/v1/tick", {
    "now": now,
    "available_triggers": [trigger["id"]]
})
print(json.dumps(tick, indent=2, ensure_ascii=False))

if tick.get("actions"):
    action = tick["actions"][0]
    print("\n9) REPLY — intent transition")
    print(json.dumps(post("/v1/reply", {
        "conversation_id": action["conversation_id"],
        "merchant_id": action["merchant_id"],
        "customer_id": action.get("customer_id"),
        "from_role": "merchant",
        "message": "ok let's do it",
        "received_at": now,
        "turn_number": 1
    }), indent=2, ensure_ascii=False))

    print("\n10) REPLY — auto-reply loop")
    for i in range(1, 3):
        result = post("/v1/reply", {
            "conversation_id": action["conversation_id"],
            "merchant_id": action["merchant_id"],
            "customer_id": action.get("customer_id"),
            "from_role": "merchant",
            "message": "Thank you for contacting us. We will get back to you.",
            "received_at": now,
            "turn_number": i + 1
        })
        print(i, result)
else:
    print("\nNo action was generated for this sample trigger.")

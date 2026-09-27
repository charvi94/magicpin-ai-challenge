import json
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE = "http://localhost:8080"
ROOT = Path(__file__).parent
EXP = ROOT / "expanded"
NOW = datetime.now(timezone.utc).isoformat()


def request(method, path, payload=None):
    data = None

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method
    )

    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def load_folder(folder):
    result = {}

    for file in (EXP / folder).glob("*.json"):
        obj = json.loads(file.read_text(encoding="utf-8"))

        if folder == "categories":
            key = obj["slug"]
        elif folder == "merchants":
            key = obj["merchant_id"]
        elif folder == "customers":
            key = obj["customer_id"]
        elif folder == "triggers":
            key = obj["id"]
        else:
            continue

        result[key] = obj

    return result


def push(scope, context_id, payload):
    return request(
        "POST",
        "/v1/context",
        {
            "scope": scope,
            "context_id": context_id,
            "version": 1,
            "payload": payload,
            "delivered_at": NOW
        }
    )


print("Loading test data...")

categories = load_folder("categories")
merchants = load_folder("merchants")
customers = load_folder("customers")
triggers = load_folder("triggers")

pairs_data = json.loads(
    (EXP / "test_pairs.json").read_text(encoding="utf-8")
)

pairs = pairs_data["pairs"]

print("Total canonical cases:", len(pairs))


# ---------------------------------------------------------
# Find only merchants/triggers/customers used by the cases
# ---------------------------------------------------------

needed_triggers = set()
needed_merchants = set()
needed_customers = set()

for pair in pairs:

    trigger_id = pair["trigger_id"]

    if trigger_id in triggers:
        trigger = triggers[trigger_id]

        needed_triggers.add(trigger_id)

        merchant_id = trigger.get("merchant_id")

        if merchant_id:
            needed_merchants.add(merchant_id)

        customer_id = trigger.get("customer_id")

        if customer_id:
            needed_customers.add(customer_id)


print("Needed merchants:", len(needed_merchants))
print("Needed customers:", len(needed_customers))
print("Needed triggers:", len(needed_triggers))


# ---------------------------------------------------------
# Push categories
# ---------------------------------------------------------

print("\nPushing categories...")

for category_id, category in categories.items():

    result = push(
        "category",
        category_id,
        category
    )

print("Categories loaded:", len(categories))


# ---------------------------------------------------------
# Push only required merchants
# ---------------------------------------------------------

print("Pushing required merchants...")

for merchant_id in needed_merchants:

    if merchant_id not in merchants:
        continue

    push(
        "merchant",
        merchant_id,
        merchants[merchant_id]
    )

print("Merchants loaded:", len(needed_merchants))


# ---------------------------------------------------------
# Push only required customers
# ---------------------------------------------------------

print("Pushing required customers...")

for customer_id in needed_customers:

    if customer_id not in customers:
        continue

    push(
        "customer",
        customer_id,
        customers[customer_id]
    )

print("Customers loaded:", len(needed_customers))


# ---------------------------------------------------------
# Test each canonical trigger
# ---------------------------------------------------------

print("\n==============================================")
print("30 CANONICAL CASES")
print("==============================================")

results = []

for index, pair in enumerate(pairs, start=1):

    trigger_id = pair["trigger_id"]

    if trigger_id not in triggers:
        print(index, trigger_id, "TRIGGER NOT FOUND")
        continue

    trigger = triggers[trigger_id]

    push(
        "trigger",
        trigger_id,
        trigger
    )

    response = request(
        "POST",
        "/v1/tick",
        {
            "now": NOW,
            "available_triggers": [trigger_id]
        }
    )

    actions = response.get("actions", [])

    if actions:

        action = actions[0]

        body = action.get("body", "")

        print(
            f"\n{index:02d}. {trigger_id}"
        )

        print(
            "    SEND:",
            body
        )

        results.append(True)

    else:

        print(
            f"\n{index:02d}. {trigger_id}"
        )

        print(
            "    EMPTY"
        )

        results.append(False)


# ---------------------------------------------------------
# Summary
# ---------------------------------------------------------

print("\n==============================================")
print("SUMMARY")
print("==============================================")

print("Total cases :", len(results))
print("Messages    :", sum(results))
print("Empty       :", len(results) - sum(results))

print("\nDone.")

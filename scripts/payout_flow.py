import sys
import time
import uuid

sys.path.insert(0, ".")
from app.paypal import PayPalSandbox

if len(sys.argv) != 2:
    sys.exit("usage: python scripts/payout_flow.py <sandbox-personal-account-email>")

pp = PayPalSandbox()
ref = f"payout-{uuid.uuid4().hex[:8]}"
batch_id = pp.payout(sys.argv[1], 108, ref=ref)
print("Payout batch:", batch_id, "| ref:", ref)

for _ in range(10):
    batch = pp.get_payout(batch_id)
    item = batch["items"][0]
    print("Batch:", batch["batch_header"]["batch_status"], "| item:", item["transaction_status"])
    if item["transaction_status"] not in ("PENDING", "ONHOLD", "NEW"):
        break
    time.sleep(5)

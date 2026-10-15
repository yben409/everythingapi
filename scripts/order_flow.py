import sys
import uuid

sys.path.insert(0, ".")
from app.paypal import PayPalSandbox

pp = PayPalSandbox()
run = uuid.uuid4().hex[:8]
order = pp.create_topup(2000, ref=f"topup-{run}")
print("Order:", order.order_id)
print("\nOpen this link, log in with the SANDBOX personal account, and approve:\n", order.approve_url)
input("\nPress Enter after approving...")
cap = pp.capture_topup(order.order_id, ref=f"capture-{run}")
print("Status:", cap.status)
print("Capture ID:", cap.capture_id, cap.amount_cents, "cents")

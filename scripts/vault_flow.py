import sys
import uuid

sys.path.insert(0, ".")
from app.paypal import PayPalSandbox

pp = PayPalSandbox()
run = uuid.uuid4().hex[:8]
order = pp.create_topup(2000, ref=f"topup-{run}", save_method=True)
print("Open in a PRIVATE window, log in with the SANDBOX PERSONAL account, accept saving PayPal, and pay:\n", order.approve_url)
input("\nPress Enter after approving...")
cap = pp.capture_topup(order.order_id, ref=f"capture-{run}")
print("Capture status:", cap.status, "| vault id:", cap.vault_id)
if cap.vault_id:
    charged = pp.charge_vaulted(cap.vault_id, 500, ref=f"auto-{run}")
    print("Second charge with NO buyer present:", charged.status, charged.amount_cents, "cents")
else:
    print("No vault id returned. Paste the output above and I will check why.")

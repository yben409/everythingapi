import json
import sys

sys.path.insert(0, ".")
from app.paypal import capture_order, charge_vaulted, create_order_save_wallet

order = create_order_save_wallet("20.00")
link = next(l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve"))
print("Open in a PRIVATE window, log in with the SANDBOX PERSONAL account, accept saving PayPal, and pay:\n", link)
input("\nPress Enter after approving...")
result = capture_order(order["id"])
print("Capture status:", result["status"])
vault = result.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault", {})
print("Vault info:", json.dumps(vault, indent=2))
vault_id = vault.get("id")
if vault_id:
    charged = charge_vaulted(vault_id, "5.00")
    if charged["status"] != "COMPLETED":
        charged = capture_order(charged["id"])
    print("Second charge with NO buyer present:", charged["status"])
else:
    print("No vault id returned. Paste the output above and I will check why.")

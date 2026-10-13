import sys

sys.path.insert(0, ".")
from app.paypal import capture_order, create_order

order = create_order("20.00")
print("Order:", order["id"], order["status"])
link = next(l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve"))
print("\nOpen this link, log in with the SANDBOX personal account, and approve:\n", link)
input("\nPress Enter after approving...")
result = capture_order(order["id"])
capture = result["purchase_units"][0]["payments"]["captures"][0]
print("Status:", result["status"])
print("Capture ID:", capture["id"], capture["amount"])

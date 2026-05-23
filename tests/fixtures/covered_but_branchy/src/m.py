"""Every line executed, but half of the branches were never taken.

This is the shape CRAP misses and riskratchet catches via `branch_gap`.
"""


def normalize(record: dict) -> dict:
    out = {}
    if "id" in record:
        out["id"] = str(record["id"])
    if "amount" in record:
        out["amount"] = float(record["amount"])
    if "currency" in record:
        out["currency"] = record["currency"].upper()
    if "captured" in record:
        out["captured"] = bool(record["captured"])
    return out

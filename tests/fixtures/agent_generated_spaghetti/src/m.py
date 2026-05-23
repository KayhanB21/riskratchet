"""The canonical 'agent wrote this and it passes its tests' failure.

Public function. High cyclomatic complexity. Long. Most branches never
taken. The kitchen-sink risk shape.
"""


def process_payment(payload: dict, *, strict: bool = False, retry: bool = False) -> dict:
    """Process a payment payload. Public, sprawling, undertested."""
    out: dict = {}
    if "id" in payload:
        out["id"] = str(payload["id"])
    if "amount" in payload:
        try:
            out["amount"] = float(payload["amount"])
        except (TypeError, ValueError):
            if strict:
                raise ValueError("bad amount") from None
            out["amount"] = 0.0
    if "currency" in payload:
        cur = payload["currency"]
        if isinstance(cur, str):
            out["currency"] = cur.upper()
        elif strict:
            raise ValueError("currency must be a string")
        else:
            out["currency"] = "USD"
    if "captured" in payload:
        out["captured"] = bool(payload["captured"])
    if "metadata" in payload:
        meta = payload["metadata"]
        if isinstance(meta, dict):
            cleaned = {}
            for key, value in meta.items():
                if value is None:
                    continue
                if isinstance(key, str):
                    cleaned[key] = str(value)
                elif strict:
                    raise ValueError(f"metadata key must be string: {key}")
            out["metadata"] = cleaned
        elif strict:
            raise ValueError("metadata must be a dict")
    if retry and "retry_count" in payload:
        try:
            out["retry_count"] = int(payload["retry_count"])
        except (TypeError, ValueError):
            out["retry_count"] = 0
    if strict and not out:
        raise ValueError("empty payload")
    return out

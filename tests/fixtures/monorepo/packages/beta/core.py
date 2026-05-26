"""Beta package — branchy, partial coverage."""


def classify(value: int) -> str:
    if value > 100:
        return "high"
    if value > 10:
        return "medium"
    if value > 0:
        return "low"
    if value == 0:
        return "zero"
    return "negative"

"""Classic CRAP case: high cyclomatic complexity, zero coverage."""


def classify(value: int) -> str:
    if value < -100:
        return "very_low"
    if value < -10:
        return "low"
    if value < 0:
        return "slightly_low"
    if value == 0:
        return "zero"
    if value < 10:
        return "slightly_high"
    if value < 100:
        return "high"
    if value < 1000:
        return "very_high"
    if value < 10_000:
        return "extreme"
    if value < 100_000:
        return "ridiculous"
    if value < 1_000_000:
        return "absurd"
    return "off_the_charts"

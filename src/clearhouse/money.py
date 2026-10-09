from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x: Decimal | int | str) -> Decimal:
    """Round to cents, halves up, the way an invoice would."""
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)

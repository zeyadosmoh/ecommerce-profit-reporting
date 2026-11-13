"""Money and VAT helpers shared by the generator. Saudi VAT is 15%; shelf prices are VAT-inclusive."""

from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = Decimal("0.15")
CENT = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    """Round to halalas (2 dp, half-up)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def split_inclusive(amount_incl: Decimal) -> tuple[Decimal, Decimal]:
    """Split a VAT-inclusive amount into (net, tax) so that net + tax == amount exactly."""
    tax = q(amount_incl * VAT_RATE / (1 + VAT_RATE))
    return amount_incl - tax, tax


def fmt(value: Decimal) -> str:
    """WooCommerce returns money as strings with 2 decimals."""
    return f"{q(value):.2f}"

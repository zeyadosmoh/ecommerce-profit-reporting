"""Static catalog definition: a Gulf lifestyle store, 40 products in 6 categories, prices in SAR incl. VAT."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

CATEGORIES: dict[str, str] = {
    "PRF": "Perfumes",
    "BKH": "Bakhoor & Incense",
    "ABY": "Abayas & Modest Wear",
    "CDT": "Coffee & Dates",
    "HOM": "Home & Majlis",
    "ACC": "Phone Accessories",
}

# (category code, name, VAT-inclusive price SAR)
PRODUCTS: list[tuple[str, str, str]] = [
    ("PRF", "Oud Royal Eau de Parfum 50ml", "389"),
    ("PRF", "Musk Al Tahara 30ml", "79"),
    ("PRF", "Amber Nights EDP 100ml", "299"),
    ("PRF", "Taifi Rose Oil 12ml", "249"),
    ("PRF", "Saffron Leather EDP 75ml", "329"),
    ("PRF", "White Musk Body Mist 250ml", "69"),
    ("PRF", "Discovery Set 5x8ml", "159"),
    ("BKH", "Bakhoor Kalimat 75g", "95"),
    ("BKH", "Bakhoor Al Arais 50g", "65"),
    ("BKH", "Cambodian Oud Chips 12g", "279"),
    ("BKH", "Electric Mabkhara USB", "119"),
    ("BKH", "Charcoal Discs x10", "15"),
    ("BKH", "Oud Muattar Tablets 40g", "85"),
    ("ABY", "Classic Black Abaya", "279"),
    ("ABY", "Linen Kimono Abaya", "349"),
    ("ABY", "Embroidered Bisht Abaya", "549"),
    ("ABY", "Everyday Crepe Abaya", "199"),
    ("ABY", "Chiffon Shayla Scarf", "59"),
    ("ABY", "Kids Prayer Set", "89"),
    ("ABY", "Modest Maxi Dress", "229"),
    ("CDT", "Khawlani Coffee Beans 500g", "69"),
    ("CDT", "Cardamom Arabic Coffee 250g", "45"),
    ("CDT", "Sukkari Dates 1kg", "55"),
    ("CDT", "Ajwa Dates 1kg", "120"),
    ("CDT", "Stuffed Dates Gift Box 24pc", "149"),
    ("CDT", "Brass Dallah Coffee Pot", "189"),
    ("CDT", "Finjan Cup Set x12", "79"),
    ("HOM", "Majlis Floor Cushion", "249"),
    ("HOM", "Sadu Pattern Throw", "179"),
    ("HOM", "Brass Lantern Medium", "139"),
    ("HOM", "Velvet Prayer Rug", "99"),
    ("HOM", "Date Serving Tray", "89"),
    ("HOM", "Oud Reed Diffuser 200ml", "109"),
    ("ACC", "MagSafe Charger 15W", "129"),
    ("ACC", "USB-C Fast Charger 30W", "79"),
    ("ACC", "Power Bank 20000mAh", "149"),
    ("ACC", "Wireless Earbuds Lite", "199"),
    ("ACC", "Leather Phone Case", "89"),
    ("ACC", "Magnetic Car Mount", "59"),
    ("ACC", "Screen Protector 2-pack", "39"),
]

# Unit cost as a share of the ex-VAT price (min, max), and base packaging cost in SAR (ex-VAT).
COST_RATIO: dict[str, tuple[float, float]] = {
    "PRF": (0.24, 0.38),
    "BKH": (0.30, 0.48),
    "ABY": (0.38, 0.52),
    "CDT": (0.45, 0.60),
    "HOM": (0.40, 0.55),
    "ACC": (0.52, 0.66),
}
PACKAGING: dict[str, Decimal] = {
    "PRF": Decimal("6.50"),
    "BKH": Decimal("4.00"),
    "ABY": Decimal("9.00"),
    "CDT": Decimal("5.00"),
    "HOM": Decimal("12.00"),
    "ACC": Decimal("3.50"),
}

COST_SHEET_START = date(2026, 1, 1)

# Mid-period cost changes: sku -> (effective_from, unit-cost multiplier, packaging delta SAR)
COST_CHANGES: dict[str, tuple[date, Decimal, Decimal]] = {
    "PRF-001": (date(2026, 8, 1), Decimal("1.12"), Decimal("0")),
    "CDT-004": (date(2026, 9, 1), Decimal("1.18"), Decimal("0")),
    "ACC-003": (date(2026, 7, 15), Decimal("0.90"), Decimal("0")),
    "ABY-001": (date(2026, 8, 15), Decimal("1.08"), Decimal("1.50")),
    "BKH-003": (date(2026, 10, 1), Decimal("1.15"), Decimal("0")),
}

# Products launched mid-period whose cost row the owner never added (must be flagged downstream).
MISSING_COST_SKUS: dict[str, date] = {
    "ACC-006": date(2026, 7, 1),
    "HOM-006": date(2026, 7, 20),
}


@dataclass(frozen=True)
class ProductDef:
    id: int
    sku: str
    name: str
    category_code: str
    price_incl: Decimal
    launch: date | None


def product_defs() -> list[ProductDef]:
    defs: list[ProductDef] = []
    counters: dict[str, int] = {}
    for idx, (cat, name, price) in enumerate(PRODUCTS):
        counters[cat] = counters.get(cat, 0) + 1
        sku = f"{cat}-{counters[cat]:03d}"
        defs.append(ProductDef(101 + idx, sku, name, cat, Decimal(price), MISSING_COST_SKUS.get(sku)))
    return defs

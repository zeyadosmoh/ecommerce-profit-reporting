"""Deterministic template summary (no AI), in English or Arabic. Every number comes straight from facts.json,
so it passes the same verifier as an LLM summary (tested)."""

ACTION_LABEL = {"en": "Recommended action:", "ar": "الإجراء المقترح:"}
POSSIBLE_WORD = {"en": "possible", "ar": "محتمل"}


def _abs_pct(change: dict) -> str | None:
    return None if change.get("pct") is None else f"{abs(change['pct']):.1f}%"


def _en_dir(change: dict) -> str:
    return {"up": "rose", "down": "fell", "flat": "was unchanged"}[change["direction"]]


def _ar_dir(change: dict) -> str:
    return {"up": "ارتفع", "down": "انخفض", "flat": "لم يتغير"}[change["direction"]]


def _thousands_ar(value: float) -> str:
    return f"{value / 1000:.1f} ألف ريال" if abs(value) >= 1000 else f"{value:,.2f} ريال"


def template_summary(facts: dict, lang: str = "en") -> list[str]:
    return _arabic(facts) if lang == "ar" else _english(facts)


def _english(f: dict) -> list[str]:
    k, ship, missing, movers = f["kpis"], f["shipping"], f["missing_cost"], f["movers"]
    rev, profit, margin = k["net_revenue"], k["net_profit"], k["net_margin_pct_known_cost"]
    bullets = [
        f"Net revenue after refunds {_en_dir(rev['change'])} {_abs_pct(rev['change'])} to SAR {rev['this_week']:,.2f} "
        f"(previous week SAR {rev['previous_week']:,.2f}); orders {_en_dir(k['orders']['change'])} "
        f"to {k['orders']['this_week']}.",
        f"Net profit on known-cost lines {_en_dir(profit['change'])} {_abs_pct(profit['change'])} to "
        f"SAR {profit['this_week']:,.2f}, while net margin on known-cost lines {_en_dir(margin['change'])} "
        f"{abs(margin['change']['abs']):.2f} percentage points to {margin['this_week']:.2f}%.",
    ]
    movers_text = []
    if movers["fallers"]:
        m = movers["fallers"][0]
        if m["sold_this_week"]:
            movers_text.append(f"the biggest profit drop was {m['product']} at SAR {m['net_profit_this_week']:,.2f} "
                               f"vs a 4-week average of SAR {m['net_profit_prev4_avg']:,.2f}")
        else:
            movers_text.append(f"the biggest profit drop was {m['product']}, with no sales this week "
                               f"(4-week average net profit SAR {m['net_profit_prev4_avg']:,.2f})")
    if movers["risers"]:
        m = movers["risers"][0]
        movers_text.append(f"the biggest gain was {m['product']} at SAR {m['net_profit_this_week']:,.2f} "
                           f"vs a 4-week average of SAR {m['net_profit_prev4_avg']:,.2f}")
    if movers_text:
        bullets.append("Product movers: " + "; ".join(movers_text) + ".")
    bullets.append(
        f"Shipping cost exceeded shipping charged by SAR {ship['subsidy']:,.2f}: SAR {ship['free_shipping_part']:,.2f} "
        f"from {ship['free_shipping_orders']} free-shipping orders and SAR {ship['paid_shipping_part']:,.2f} from "
        f"paid-shipping orders; the free-shipping threshold is a possible lever on profit.")
    if missing["lines_this_week"]:
        bullets.append(f"{missing['lines_this_week']} order lines (SAR {missing['net_revenue_this_week']:,.2f} of "
                       f"revenue) have no cost row and are excluded from profit, not counted as zero cost.")
        skus = ", ".join(s["sku"] for s in missing["skus"])
        bullets.append(f"{ACTION_LABEL['en']} add cost rows for {skus} to the cost sheet so their profit can be measured.")
    else:
        bullets.append(f"{ACTION_LABEL['en']} review the free-shipping threshold against the shipping subsidy above.")
    return bullets[:6]


def _arabic(f: dict) -> list[str]:
    k, ship, missing, movers = f["kpis"], f["shipping"], f["missing_cost"], f["movers"]
    rev, profit, margin = k["net_revenue"], k["net_profit"], k["net_margin_pct_known_cost"]
    bullets = [
        f"{_ar_dir(rev['change'])} صافي الإيرادات بعد المرتجعات بنسبة {_abs_pct(rev['change'])} إلى "
        f"{_thousands_ar(rev['this_week'])} (الأسبوع السابق {_thousands_ar(rev['previous_week'])})، "
        f"وعدد الطلبات {k['orders']['this_week']}.",
        f"{_ar_dir(profit['change'])} صافي الربح على البنود معروفة التكلفة بنسبة {_abs_pct(profit['change'])} إلى "
        f"{profit['this_week']:,.2f} ريال، في حين {_ar_dir(margin['change'])} هامش صافي الربح "
        f"{abs(margin['change']['abs']):.2f} نقطة مئوية إلى {margin['this_week']:.2f}%.",
    ]
    if movers["fallers"]:
        m = movers["fallers"][0]
        if m["sold_this_week"]:
            bullets.append(f"أكبر تراجع في الربح: {m['product']} بصافي ربح {m['net_profit_this_week']:,.2f} ريال "
                           f"مقابل متوسط 4 أسابيع {m['net_profit_prev4_avg']:,.2f} ريال.")
        else:
            bullets.append(f"أكبر تراجع في الربح: {m['product']}، بلا مبيعات هذا الأسبوع "
                           f"(متوسط صافي الربح في 4 أسابيع {m['net_profit_prev4_avg']:,.2f} ريال).")
    bullets.append(
        f"تكلفة الشحن تجاوزت رسوم الشحن المحصّلة بمبلغ {ship['subsidy']:,.2f} ريال: {ship['free_shipping_part']:,.2f} "
        f"ريال من {ship['free_shipping_orders']} طلب بشحن مجاني و{ship['paid_shipping_part']:,.2f} ريال من طلبات "
        f"الشحن المدفوع؛ حدّ الشحن المجاني سبب محتمل للضغط على الربح.")
    if missing["lines_this_week"]:
        bullets.append(f"{missing['lines_this_week']} بند طلب ({missing['net_revenue_this_week']:,.2f} ريال من "
                       f"الإيرادات) بلا سطر تكلفة، لذلك استُبعدت من الربح ولم تُحسب تكلفتها صفراً.")
        skus = "، ".join(s["sku"] for s in missing["skus"])
        bullets.append(f"{ACTION_LABEL['ar']} أضف أسطر التكلفة للمنتجات {skus} إلى ورقة التكاليف لقياس ربحها.")
    else:
        bullets.append(f"{ACTION_LABEL['ar']} راجع حدّ الشحن المجاني مقارنة بدعم الشحن أعلاه.")
    return bullets[:6]

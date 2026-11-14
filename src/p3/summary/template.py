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


DRIVER_LABELS_AR = {
    "net_revenue_known_cost_ex_vat": "صافي الإيرادات بعد المرتجعات (التكلفة المعروفة)",
    "cogs_known_cost": "تكلفة البضاعة المباعة",
    "shipping_charged_known_cost_ex_vat": "رسوم الشحن المحصّلة",
    "shipping_refunds_known_cost_ex_vat": "رسوم الشحن المستردة",
    "shipping_cost_known_cost_ex_vat": "تكلفة الشحن",
    "payment_fees_known_cost": "رسوم الدفع",
}


def _top_drivers(f: dict, n: int = 2) -> list[dict]:
    return [d for d in f["profit_drivers"]["drivers"] if d["effect"] != "none"][:n]


def _english(f: dict) -> list[str]:
    k, ship, missing, movers = f["kpis"], f["shipping"], f["missing_cost"], f["movers"]
    profit, margin = k["net_profit"], k["net_margin_pct_known_cost"]
    drivers = "; ".join(
        f"{d['label']} {_en_dir(d['change'])} SAR {abs(d['change']['abs']):,.2f}, which {d['effect']} profit"
        for d in _top_drivers(f))
    bullets = [
        f"Net profit on known-cost lines {_en_dir(profit['change'])} {_abs_pct(profit['change'])} "
        f"(SAR {abs(profit['change']['abs']):,.2f}) to SAR {profit['this_week']:,.2f}, while net margin on known-cost "
        f"lines {_en_dir(margin['change'])} {abs(margin['change']['abs']):.2f} percentage points to {margin['this_week']:.2f}%.",
        f"Largest profit drivers: {drivers}. Orders {_en_dir(k['orders']['change'])} from "
        f"{k['orders']['previous_week']} to {k['orders']['this_week']}.",
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
                       f"revenue) are excluded from profit because they have no cost row; this is a data gap, "
                       f"not a driver of the weekly change.")
        skus = ", ".join(s["sku"] for s in missing["skus"])
        bullets.append(f"{ACTION_LABEL['en']} add cost rows for {skus} to the cost sheet so their profit can be measured.")
    else:
        bullets.append(f"{ACTION_LABEL['en']} review the free-shipping threshold against the shipping subsidy above.")
    return bullets[:6]


def _arabic(f: dict) -> list[str]:
    k, ship, missing, movers = f["kpis"], f["shipping"], f["missing_cost"], f["movers"]
    profit, margin = k["net_profit"], k["net_margin_pct_known_cost"]
    effect_ar = {"helped": "مما دعم الربح", "hurt": "مما أضر بالربح"}
    drivers = "؛ ".join(
        f"{_ar_dir(d['change'])} {DRIVER_LABELS_AR[d['id']]} {abs(d['change']['abs']):,.2f} ريال {effect_ar[d['effect']]}"
        for d in _top_drivers(f))
    bullets = [
        f"{_ar_dir(profit['change'])} صافي الربح على البنود معروفة التكلفة بنسبة {_abs_pct(profit['change'])} "
        f"({abs(profit['change']['abs']):,.2f} ريال) إلى {_thousands_ar(profit['this_week'])}، في حين "
        f"{_ar_dir(margin['change'])} هامش صافي الربح {abs(margin['change']['abs']):.2f} نقطة مئوية إلى "
        f"{margin['this_week']:.2f}%.",
        f"أكبر محركات الربح: {drivers}. وتغير عدد الطلبات من {k['orders']['previous_week']} إلى {k['orders']['this_week']}.",
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
                       f"الإيرادات) استُبعدت من الربح بسبب عدم وجود سطر تكلفة؛ هذه فجوة بيانات وليست محركاً "
                       f"للتغير الأسبوعي.")
        skus = "، ".join(s["sku"] for s in missing["skus"])
        bullets.append(f"{ACTION_LABEL['ar']} أضف أسطر التكلفة للمنتجات {skus} إلى ورقة التكاليف لقياس ربحها.")
    else:
        bullets.append(f"{ACTION_LABEL['ar']} راجع حدّ الشحن المجاني مقارنة بدعم الشحن أعلاه.")
    return bullets[:6]

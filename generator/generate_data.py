"""
NordPack Manufacturing s.r.o. – generátor syntetických nákupních dat.

Spuštění (z kořene projektu):
    python generator/generate_data.py

Data vznikají ve stejném pořadí jako v reálné firmě:
  krok 2: kmenová data (master data) – dodavatelé, materiály, kurzy, standardní ceny
  krok 3: objednávky
  krok 4: příjmy a faktury
  krok 5: spotřeba a zásoby
  krok 6: chyby v datech + export do CSV a SQLite

Konvence: sloupce začínající podtržítkem (např. _base_price_czk) jsou "skrytá pravda"
generátoru – informace, kterou v reálné firmě v systému nevidíš (např. kdo je problémový
dodavatel). Generátor je používá, ale do CSV/SQLite se neexportují.
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]   # kořen projektu procurement-analytics/


def load_config():
    with open(ROOT / "generator" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def allocate(total, shares, minimum=0):
    """Rozdělí celé číslo `total` podle podílů tak, aby součet přesně seděl.
    Příklad: allocate(40, {"A": 0.6, "B": 0.4}) -> {"A": 24, "B": 16}"""
    names = list(shares)
    counts = np.maximum(np.round(np.array([shares[n] for n in names]) * total).astype(int), minimum)
    counts[np.argmax(counts)] += total - counts.sum()   # dorovnání zaokrouhlení u největší skupiny
    return dict(zip(names, (int(c) for c in counts)))


def nice_floor(x):
    """Zaokrouhlí dolů na 'hezké' číslo 1–2–5 (MOQ bývá 500, 1000, 2000, ne 1374)."""
    if x < 1:
        return 1
    power = 10 ** int(np.floor(np.log10(x)))
    for step in (5, 2, 1):
        if x >= step * power:
            return step * power


def days_since(dates, origin="2024-01-01"):
    """Počet dní od pevného data – pomocná funkce pro výpočty s časem."""
    return np.asarray((pd.DatetimeIndex(dates) - pd.Timestamp(origin)).days, dtype=float)


# =============================================================================
# KROK 2 – KMENOVÁ DATA (MASTER DATA)
# =============================================================================

# Vymyšlené názvy – záměrně nepodobné skutečným firmám (projekt je veřejný).
VENDOR_PREFIXES = [
    "Alvora", "Brenix", "Corvalt", "Delmora", "Eskarn", "Fenwyx", "Galdrin", "Hollvik",
    "Istrel", "Jorvan", "Kaldrin", "Lumora", "Marvex", "Norvald", "Ostrel", "Pelvado",
    "Quorvis", "Rovanta", "Seltrix", "Tarvik", "Ulmora", "Vexalt", "Wendrix", "Zorvane",
    "Arkelo", "Belvant", "Cindral", "Dravik", "Elvont", "Fyrena", "Gorvel", "Halvex",
    "Irvano", "Jelmar", "Kestrel", "Lindrax", "Mervale", "Nolvik", "Orvento", "Prevalt",
]
CATEGORY_WORD = {
    "Folding cartons": "Packaging", "Printed inserts": "Print", "PE bags & film": "Plast",
    "Labels": "Label", "Pallets": "Pallets", "Stretch film": "Film", "Adhesive tapes": "Tape",
    "Energy": "Energie", "Facility services": "Facility", "Construction": "Stavby",
    "Professional svc": "Consulting",
}
LEGAL_FORM = {"CZ": "s.r.o.", "SK": "s.r.o.", "DE": "GmbH", "AT": "GmbH", "NL": "B.V.", "PL": "Sp. z o.o."}

# SAP typ materiálu (MTART): VERP = obaly (skladový, oceňovaný standardní cenou),
# DIEN = služby, NLAG = neskladový materiál (energie). Služby a energie nemají zásobu.
MATERIAL_TYPE = {"direct": "VERP", "Energy": "NLAG", "indirect": "DIEN"}


def gen_vendors(cfg, rng):
    """LFA1 – kmenová data dodavatelů."""
    cats = cfg["categories"]
    n_total = cfg["counts"]["vendors"]
    # Kolik dodavatelů na kategorii – podle podílu, ale aspoň 2 (aby šlo porovnávat).
    n_per_cat = allocate(n_total, {c: v["share"] for c, v in cats.items()}, minimum=2)

    # Měny: nepřímý nákup (energie, služby, stavby) je lokální → vždy CZK.
    # Zbylé měny rozdělíme mezi dodavatele přímého materiálu, aby celkový mix seděl s configem.
    n_indirect = sum(n for c, n in n_per_cat.items() if cats[c]["type"] == "indirect")
    cur_counts = allocate(n_total, cfg["vendor_currency_share"])
    cur_counts["CZK"] -= n_indirect
    direct_currencies = iter(rng.permutation([cur for cur, n in cur_counts.items() for _ in range(n)]))

    prefixes = rng.permutation(VENDOR_PREFIXES)
    rows = []
    for cat, n in n_per_cat.items():
        for k in range(n):
            currency = "CZK" if cats[cat]["type"] == "indirect" else str(next(direct_currencies))
            country = str(rng.choice(cfg["vendor_countries"][currency]))
            i = len(rows)
            rows.append({
                "vendor_id": f"V{10001 + i}",
                "vendor_name": f"{prefixes[i]} {CATEGORY_WORD[cat]} {LEGAL_FORM[country]}",
                "country": country,
                "currency": currency,
                "payment_terms": str(rng.choice(cfg["payment_terms"])),
                "_category": cat,
                # první dodavatel kartonů = velký dodavatel s horším OTIF
                "_is_late_vendor": cat == cfg["late_vendor"]["category"] and k == 0,
            })
    return pd.DataFrame(rows)


def describe(cat, rng):
    """Vymyslí realistický popis materiálu podle kategorie."""
    r = lambda a, b: int(rng.integers(a, b + 1))
    pick = lambda options: str(rng.choice(options))
    area = pick(["Hall A", "Hall B", "Warehouse", "Offices", "Plant site"])
    if cat == "Folding cartons":
        return f"Folding carton {r(10, 40) * 10}x{r(8, 30) * 10}x{r(4, 20) * 10} mm {pick(['E-flute', 'B-flute', 'GC2 board', 'GD2 board'])}"
    if cat == "Printed inserts":
        return f"Printed insert {pick(['A4', 'A5', 'A6', 'DL'])} {pick(['1C', '2C', '4C'])} {pick(['80g', '100g', '120g'])}"
    if cat == "PE bags & film":
        return pick([f"PE bag {r(10, 60) * 10}x{r(15, 80) * 10} mm {r(3, 10) * 10}um",
                     f"PE film {r(5, 20) * 100} mm {r(3, 10) * 10}um"])
    if cat == "Labels":
        return f"Label {r(3, 15) * 10}x{r(2, 10) * 10} mm {pick(['paper', 'PP', 'thermal'])}"
    if cat == "Pallets":
        return f"{pick(['EUR pallet 1200x800', 'Half pallet 800x600', 'Industrial pallet 1200x1000'])} {pick(['new', 'repaired', 'heat-treated'])}"
    if cat == "Stretch film":
        return f"Stretch film {pick([450, 500])} mm {r(12, 30)}um {pick(['hand', 'machine', 'pre-stretch'])}"
    if cat == "Adhesive tapes":
        return f"Tape {pick(['PP', 'PVC', 'paper'])} {pick([38, 48, 50, 75])} mm x {pick([50, 66, 100])} m ({pick([6, 24, 36])} rolls)"
    if cat == "Energy":
        return f"{pick(['Electricity supply', 'Natural gas supply', 'Electricity distribution', 'Heat supply'])} - {area}"
    if cat == "Facility services":
        return f"{pick(['Cleaning services', 'Security services', 'Waste disposal', 'HVAC maintenance', 'Canteen services', 'Pest control'])} - {area}"
    if cat == "Construction":
        return f"{pick(['Roof repair', 'Floor renovation', 'Loading dock extension', 'Sprinkler upgrade', 'Office refurbishment', 'Parking lot extension'])} - {area}"
    return pick(["IT consulting", "Legal services", "Audit services", "Engineering consulting", "Training services", "Temporary staff"])


def gen_materials(cfg, rng, vendors):
    """MARA – kmenová data materiálů (+ skryté údaje: základní cena, dodavatel, roční spotřeba)."""
    cats = cfg["categories"]
    n_per_cat = allocate(cfg["counts"]["materials"], {c: v["share"] for c, v in cats.items()}, minimum=1)
    rows = []
    for cat, n in n_per_cat.items():
        c = cats[cat]
        cat_vendors = vendors[vendors["_category"] == cat]
        # Velký dodavatel kartonů dostane ~3× víc materiálů než ostatní → velký podíl na spendu.
        weights = np.where(cat_vendors["_is_late_vendor"], 3.0, 1.0)
        weights = weights / weights.sum()
        lt_range = cfg["delivery"]["lead_time_days" if c["type"] == "direct" else "lead_time_days_indirect"]
        for _ in range(n):
            lo, hi = c["price"]
            rows.append({
                "material_id": f"M{100001 + len(rows)}",
                "description": describe(cat, rng),
                "category": cat,
                "spend_type": c["type"],
                "material_type": MATERIAL_TYPE["direct"] if c["type"] == "direct"
                                 else MATERIAL_TYPE.get(cat, MATERIAL_TYPE["indirect"]),
                "uom": c["uom"],
                "lead_time_days": int(rng.integers(lt_range[0], lt_range[1] + 1)),
                # log-uniformní rozdělení: víc levných položek než drahých (jako v realitě)
                "_base_price_czk": float(np.exp(rng.uniform(np.log(lo), np.log(hi)))),
                "_vendor_id": str(rng.choice(cat_vendors["vendor_id"], p=weights)),
            })
    mat = pd.DataFrame(rows)

    # Roční spend na materiál: log-normální rozdělení → pár materiálů tvoří většinu spendu
    # (Pareto 80/20). Pak spend / cena = roční spotřeba (D pro EOQ).
    total = cfg["annual_spend_czk"]
    budget = {"direct": total * cfg["direct_spend_share"], "indirect": total * (1 - cfg["direct_spend_share"])}
    raw = rng.lognormal(mean=0, sigma=1.2, size=len(mat))
    for st, amount in budget.items():
        mask = mat["spend_type"] == st
        mat.loc[mask, "_annual_spend_czk"] = raw[mask] / raw[mask].sum() * amount
    mat["_annual_demand"] = np.maximum(1, np.round(mat["_annual_spend_czk"] / mat["_base_price_czk"]))

    # MOQ (minimální objednací množství) = cca 1–3 týdny spotřeby, zaokrouhleno na "hezké" číslo.
    # Služby a energie MOQ nemají (= 1).
    mat["moq"] = [
        nice_floor(d / rng.uniform(20, 60)) if st == "direct" else 1
        for d, st in zip(mat["_annual_demand"], mat["spend_type"])
    ]
    return mat


def gen_fx_rates(cfg, rng):
    """TCURR – měsíční kurzy do CZK.
    actual_rate = skutečný průměrný kurz měsíce (SAP typ kurzu M)
    plan_rate   = plánovací kurz na rok (SAP typ kurzu P) = průměr skutečných kurzů minulého roku"""
    months = pd.date_range(cfg["fx"]["history_start"], cfg["period"]["end"], freq="MS")
    frames = []
    for cur in ("EUR", "PLN"):
        p = cfg["fx"][cur]
        # Náhodná procházka (random walk): každý měsíc kurz změní o náhodné % ± volatilita.
        shocks = rng.normal(0, p["monthly_volatility"], len(months))
        shocks[0] = 0
        rate = p["start"] * np.exp(np.cumsum(shocks))
        df = pd.DataFrame({"month": months, "from_currency": cur, "to_currency": "CZK", "actual_rate": rate})
        yearly_avg = df.groupby(df["month"].dt.year)["actual_rate"].mean()
        df["plan_rate"] = [yearly_avg.get(m.year - 1, p["start"]) for m in df["month"]]
        frames.append(df)
    fx = pd.concat(frames, ignore_index=True)
    fx[["actual_rate", "plan_rate"]] = fx[["actual_rate", "plan_rate"]].round(4)
    return fx


def price_index(cfg, category, dates):
    """Cenový index kategorie k danému datu (1.00 = základní cena na začátku období)."""
    trend = cfg["categories"][category]["trend"]
    if trend == "carton":
        anchors = cfg["carton_price_index"]                   # {2024: 1.00, 2025: 1.09, ...}
        x = days_since([f"{y}-12-31" for y in anchors])
        return np.interp(days_since(dates), x, list(anchors.values()))  # lineárně mezi body
    years = (days_since(dates) - days_since([cfg["period"]["start"]])[0]) / 365.25
    return (1 + trend) ** years                               # pravidelný roční růst/pokles


def gen_standard_prices(cfg, rng, materials):
    """MBEW – standardní cena (jen přímý materiál; služby a energie se standardní cenou neoceňují).
    Nastavuje se 1× ročně v lednu = průměrná tržní cena minulého roku ± malá odchylka."""
    start, end = pd.Timestamp(cfg["period"]["start"]), pd.Timestamp(cfg["period"]["end"])
    noise = cfg["standard_price"]["noise"]
    rows = []
    for _, m in materials[materials["spend_type"] == "direct"].iterrows():
        for year in range(start.year, end.year + 1):
            if year == start.year:
                level = 1.0                                   # první rok = základní cena
            else:
                prev_months = pd.date_range(f"{year - 1}-01-01", f"{year - 1}-12-01", freq="MS")
                level = price_index(cfg, m["category"], prev_months).mean()
            rows.append({
                "material_id": m["material_id"],
                "valuation_year": year,
                "valid_from": pd.Timestamp(year, 1, 1),
                "price_control": "S",                        # S = standardní cena (V = klouzavý průměr)
                "standard_price_czk": round(m["_base_price_czk"] * level * (1 + rng.uniform(-noise, noise)), 4),
            })
    return pd.DataFrame(rows)


# =============================================================================
# KROK 3 – OBJEDNÁVKY (EKKO hlavička + EKPO položky)
# =============================================================================

# Nepřímý nákup se neplánuje přes zásobu, ale v pravidelném rytmu.
INDIRECT_ORDERS_PER_YEAR = {"Energy": 12, "Facility services": 12, "Professional svc": 4}


def assign_behaviour(cfg, rng, materials):
    """Každému přímému materiálu přiřadí 'chování' = zasazený vzorec pro dashboard:
      slow_mover – spotřeba v určitý den skončí → ležák
      overstock  – poslední objednávka je 4–6× větší → nadměrná zásoba
      over_eoq   – systematicky moc velké dávky (2–3× EOQ)
      under_eoq  – systematicky moc malé dávky (0,3–0,5× EOQ)
      normal     – objednává se kolem EOQ
    A spočítá EOQ = sqrt(2·D·S / H)."""
    mat = materials.copy()
    direct_ids = rng.permutation(mat.loc[mat["spend_type"] == "direct", "material_id"])
    n = len(direct_ids)
    sizes = {
        "slow_mover": round(n * cfg["stock"]["slow_mover_share"]),
        "overstock": cfg["stock"]["overstock_materials"],
        "over_eoq": round(n * cfg["batch"]["over_eoq_share"]),
        "under_eoq": round(n * cfg["batch"]["under_eoq_share"]),
    }
    behaviour, pos = {}, 0
    for name, k in sizes.items():
        behaviour.update({mid: name for mid in direct_ids[pos:pos + k]})
        pos += k
    behaviour.update({mid: "normal" for mid in direct_ids[pos:]})
    mat["_behaviour"] = mat["material_id"].map(behaviour).fillna("indirect")

    # EOQ: D = roční spotřeba, S = náklad na objednávku, H = roční náklad držení 1 ks
    S = cfg["batch"]["ordering_cost_czk"]
    H = mat["_base_price_czk"] * cfg["batch"]["holding_rate"]
    mat["_eoq"] = np.sqrt(2 * mat["_annual_demand"] * S / H)
    factor = {"over_eoq": (2, 3), "under_eoq": (0.3, 0.5)}
    mat["_batch"] = [max(moq, eoq * rng.uniform(*factor.get(b, (0.8, 1.25))))
                     for moq, eoq, b in zip(mat["moq"], mat["_eoq"], mat["_behaviour"])]

    # Ležáky: den, kdy přestala spotřeba (200–400 dní před koncem období)
    end = pd.Timestamp(cfg["period"]["end"])
    lo, hi = cfg["stock"]["slow_mover_stop_before_end_days"]
    mat["_stop_date"] = [end - pd.Timedelta(days=int(rng.integers(lo, hi + 1))) if b == "slow_mover" else pd.NaT
                         for b in mat["_behaviour"]]
    return mat


def round_qty(q, moq):
    """Objednací množství: aspoň MOQ, zaokrouhleno nahoru na 2 platné číslice (12 345 → 13 000)."""
    q = max(q, moq, 1)
    if q < 10:
        return float(np.ceil(q))
    step = 10 ** (int(np.floor(np.log10(q))) - 1)
    return float(np.ceil(q / step) * step)


def daily_demand(cfg, rng, m, days):
    """Denní spotřeba materiálu = roční spotřeba / 365 × sezónnost × týdenní šum.
    Stejnou řadu použijeme v kroku 5 pro spotřebu (MSEG 261), aby zásoby seděly."""
    season = np.array([cfg["seasonality"][mm] for mm in days.month])
    week = np.asarray((days - days[0]).days // 7)
    sigma = 0.25
    weekly_noise = rng.lognormal(0, sigma, week.max() + 1) / np.exp(sigma ** 2 / 2)  # průměr = 1
    d = m["_annual_demand"] / 365 * season * weekly_noise[week]
    if m["_behaviour"] == "slow_mover":
        d[days >= m["_stop_date"]] = 0
    return d


def schedule_direct(rng, m, days, demand):
    """Zjednodušené MRP: objednávka vznikne, když by zásoba klesla pod pojistnou zásobu (14 dní).
    Vrací seznam objednávek a počáteční zásobu (pro krok 5)."""
    cum = np.cumsum(demand)                       # kumulovaná spotřeba od začátku období
    daily_avg = m["_annual_demand"] / 365
    safety = daily_avg * 14
    initial = daily_avg * (m["lead_time_days"] + 14) + m["_batch"] * rng.uniform(0.2, 0.6)
    covered, orders = initial, []                 # covered = počáteční zásoba + vše objednané
    while True:
        i = np.searchsorted(cum, covered - safety)  # první den, kdy by zásoba klesla pod pojistnou
        if i >= len(days):
            break
        need = days[i]
        qty = round_qty(m["_batch"] * rng.uniform(0.9, 1.1), m["moq"])
        order_date = max(days[0], need - pd.Timedelta(days=int(m["lead_time_days"] + rng.integers(0, 6))))
        orders.append({"material_id": m["material_id"], "order_date": order_date,
                       "requested_delivery_date": need, "quantity": qty})
        covered += qty
    if m["_behaviour"] == "overstock" and orders:
        orders[-1]["quantity"] = round_qty(orders[-1]["quantity"] * rng.uniform(4, 6), m["moq"])
    return orders, initial


def schedule_indirect(rng, m, start, end):
    """Nepřímý nákup: pravidelné objednávky (měsíčně / čtvrtletně), stavby po projektech (množství 1)."""
    if m["category"] == "Construction":
        per_year, base_qty = max(1.0, m["_annual_demand"]), 1.0
    else:
        per_year = INDIRECT_ORDERS_PER_YEAR[m["category"]]
        base_qty = m["_annual_demand"] / per_year
    interval = 365 / per_year
    orders, t = [], rng.uniform(0, interval)
    while t < (end - start).days:
        need = start + pd.Timedelta(days=int(t))
        qty = 1.0 if m["category"] == "Construction" else round_qty(base_qty * rng.uniform(0.85, 1.15), 1)
        orders.append({"material_id": m["material_id"],
                       "order_date": max(start, need - pd.Timedelta(days=int(m["lead_time_days"]))),
                       "requested_delivery_date": need, "quantity": qty})
        t += interval
    return orders


def gen_purchase_orders(cfg, rng, materials, vendors, fx):
    """Vytvoří položky objednávek, ocení je a seskupí do objednávek (EKKO + EKPO)."""
    start, end = pd.Timestamp(cfg["period"]["start"]), pd.Timestamp(cfg["period"]["end"])
    days = pd.date_range(start, end, freq="D")
    demand, initial_stock, lines = {}, {}, []
    for _, m in materials.iterrows():
        if m["spend_type"] == "direct":
            demand[m["material_id"]] = daily_demand(cfg, rng, m, days)
            orders, initial_stock[m["material_id"]] = schedule_direct(rng, m, days, demand[m["material_id"]])
        else:
            orders = schedule_indirect(rng, m, start, end)
        lines.extend(orders)
    lines = pd.DataFrame(lines)
    mat = materials.set_index("material_id")
    lines["vendor_id"] = lines["material_id"].map(mat["_vendor_id"])
    lines["currency"] = lines["vendor_id"].map(vendors.set_index("vendor_id")["currency"])

    # --- Cena: tržní cena v CZK = základní cena × cenový index kategorie k datu objednávky ± šum
    market_czk = pd.Series(0.0, index=lines.index)
    for cat, idx in lines.groupby(lines["material_id"].map(mat["category"])).groups.items():
        market_czk[idx] = lines.loc[idx, "material_id"].map(mat["_base_price_czk"]) \
                          * price_index(cfg, cat, lines.loc[idx, "order_date"])
    noise = cfg["po_price_noise"]
    price_czk = market_czk * (1 + rng.uniform(-noise, noise, len(lines)))
    # Zahraniční dodavatel: cena v EUR/PLN dohodnutá přes plánovací kurz roku.
    # V korunách se pak mění se skutečným kurzem → kurzový efekt v PPV.
    plan = fx.groupby(["from_currency", fx["month"].dt.year])["plan_rate"].first().to_dict()
    rate = [1.0 if c == "CZK" else plan[(c, d.year)] for c, d in zip(lines["currency"], lines["order_date"])]
    lines["net_price"] = (price_czk / rate).round(6)   # 6 míst: levné položky v EUR stojí i 0,004
    lines["net_value"] = (lines["net_price"] * lines["quantity"]).round(2)

    # --- Seskupení do objednávek: stejný dodavatel + stejný týden → 1 objednávka s 1–5 položkami
    lines = lines.sort_values(["vendor_id", "order_date"]).reset_index(drop=True)
    max_items = cfg["counts"]["items_per_po"][1]
    po_key, key = np.zeros(len(lines), dtype=int), 0
    for _, grp in lines.groupby(["vendor_id", lines["order_date"].dt.to_period("W")], sort=False):
        idxs = list(grp.index)
        while idxs:
            k = int(rng.integers(1, max_items + 1))
            key += 1
            po_key[idxs[:k]] = key
            idxs = idxs[k:]
    lines["_po_key"] = po_key
    lines["po_date"] = lines.groupby("_po_key")["order_date"].transform("min")
    # Čísla objednávek ve stylu SAP podle data: 4500000001, 4500000002, ...
    order = lines.groupby("_po_key")["po_date"].first().sort_values(kind="stable")
    lines["po_number"] = lines["_po_key"].map(pd.Series(4500000001 + np.arange(len(order)), index=order.index)).astype(str)
    lines = lines.sort_values(["po_number", "material_id"]).reset_index(drop=True)
    lines["po_item"] = (lines.groupby("po_number").cumcount() + 1) * 10   # SAP číslování 10, 20, 30

    # Nákupčí podle kategorie: přímý materiál rozdělen mezi první 3, nepřímý má poslední nákupčí
    buyers = cfg["buyers"]
    direct_cats = [c for c, v in cfg["categories"].items() if v["type"] == "direct"]
    buyer_of = {c: buyers[i % (len(buyers) - 1)] for i, c in enumerate(direct_cats)}
    vendor_cat = vendors.set_index("vendor_id")["_category"]

    header = (lines.groupby("po_number", as_index=False)
              .agg(po_date=("po_date", "first"), vendor_id=("vendor_id", "first"), currency=("currency", "first")))
    header["buyer"] = header["vendor_id"].map(vendor_cat).map(buyer_of).fillna(buyers[-1])
    items = lines.assign(plant=cfg["company"]["plant"], order_uom=lines["material_id"].map(mat["uom"]))[
        ["po_number", "po_item", "material_id", "plant", "quantity", "order_uom",
         "net_price", "net_value", "requested_delivery_date"]]
    return header, items, demand, initial_stock


# =============================================================================
# KROK 4 – PŘÍJMY (MSEG/MATDOC, pohyb 101) A FAKTURY (RBKP/RSEG, MIRO)
# =============================================================================

def rate_lookup(fx):
    """Slovník {(měna, první den měsíce): skutečný kurz} pro rychlé přepočty do CZK."""
    return {(c, m): r for c, m, r in zip(fx["from_currency"], fx["month"], fx["actual_rate"])}


def to_czk(rates, currency, day):
    return 1.0 if currency == "CZK" else rates[(currency, day.replace(day=1))]


def gen_goods_receipts(cfg, rng, header, items, vendors, fx):
    """Příjem na sklad (goods receipt, pohyb 101) ke každé položce objednávky.
    - včas: dodávka v požadovaný den nebo až 3 dny dřív
    - pozdě: o X dní později (problémový dodavatel častěji a víc)
    - částečná dodávka: nejdřív přijde 70–95 %, zbytek o 5–15 dní později (doobjednávka / backorder)
    Příjmy po konci období nevzniknou → objednávka zůstane otevřená."""
    end = pd.Timestamp(cfg["period"]["end"])
    dcfg, lcfg = cfg["delivery"], cfg["late_vendor"]
    late_vendors = set(vendors.loc[vendors["_is_late_vendor"], "vendor_id"])
    df = items.merge(header[["po_number", "po_date", "vendor_id", "currency"]], on="po_number")
    rows = []
    for r in df.itertuples(index=False):
        p = lcfg if r.vendor_id in late_vendors else dcfg
        if rng.random() < p["on_time_rate"]:
            gr_date = r.requested_delivery_date - pd.Timedelta(days=int(rng.integers(0, 4)))
        else:
            gr_date = r.requested_delivery_date + pd.Timedelta(days=int(rng.integers(p["late_days"][0], p["late_days"][1] + 1)))
        gr_date = max(gr_date, r.po_date + pd.Timedelta(days=1))   # nelze přijmout dřív, než se objednalo
        deliveries = [(gr_date, r.quantity)]
        if r.quantity >= 10 and rng.random() < dcfg["partial_delivery_rate"]:
            first = float(np.floor(r.quantity * rng.uniform(*dcfg["partial_share"])))
            deliveries = [(gr_date, first),
                          (gr_date + pd.Timedelta(days=int(rng.integers(5, 16))), r.quantity - first)]
        for posting_date, qty in deliveries:
            rows.append({"po_number": r.po_number, "po_item": r.po_item, "material_id": r.material_id,
                         "plant": r.plant, "posting_date": posting_date, "quantity": qty,
                         "_net_price": r.net_price, "_currency": r.currency})
    gr = pd.DataFrame(rows)
    gr = gr[gr["posting_date"] <= end].sort_values(["posting_date", "po_number", "po_item"]).reset_index(drop=True)

    # Hodnota příjmu v Kč = množství × cena z objednávky × kurz ke dni příjmu (SAP: DMBTR).
    rates = rate_lookup(fx)
    gr["amount_czk"] = [round(q * p * to_czk(rates, c, d), 2)
                        for q, p, c, d in zip(gr["quantity"], gr["_net_price"], gr["_currency"], gr["posting_date"])]
    gr.insert(0, "gr_id", (5000000001 + gr.index).astype(str))   # číslo materiálového dokladu
    gr["movement_type"] = "101"
    return gr


def gen_invoices(cfg, rng, grs, header, fx):
    """Faktura dodavatele (MIRO) ke každému příjmu.
    ~90 % faktur má cenu podle objednávky, ~10 % má odchylku (IPV = Invoice Price Variance).
    Odchylka nad toleranci → blokace k platbě (payment block "R"), nákupčí musí řešit s dodavatelem."""
    end = pd.Timestamp(cfg["period"]["end"])
    icfg = cfg["invoices"]
    vendor_of = header.set_index("po_number")["vendor_id"]
    rates = rate_lookup(fx)
    rows = []
    # itertuples neumí sloupce začínající "_" → dočasně přejmenujeme
    for r in grs.rename(columns={"_net_price": "net_price", "_currency": "currency"}).itertuples(index=False):
        inv_date = r.posting_date + pd.Timedelta(days=int(rng.integers(icfg["invoice_delay_days"][0],
                                                                       icfg["invoice_delay_days"][1] + 1)))
        if inv_date > end:
            continue                                   # faktura ještě nepřišla
        deviation = 0.0
        if rng.random() < icfg["price_deviation_rate"]:
            deviation = rng.uniform(*icfg["price_deviation"])
            if abs(deviation) < 0.005:                 # odchylka aspoň 0,5 %, ať je vidět
                deviation = 0.005 if deviation >= 0 else -0.005
        unit_price = round(r.net_price * (1 + deviation), 6)
        amount = round(r.quantity * unit_price, 2)
        rows.append({
            "invoice_date": inv_date, "vendor_id": vendor_of[r.po_number],
            "po_number": r.po_number, "po_item": r.po_item, "gr_id": r.gr_id, "material_id": r.material_id,
            "quantity": r.quantity, "unit_price": unit_price,
            "amount": amount, "currency": r.currency,
            "amount_czk": round(amount * to_czk(rates, r.currency, inv_date), 2),   # kurz ke dni faktury
            "payment_block": "R" if abs(deviation) > icfg["tolerance"] else "",
        })
    inv = pd.DataFrame(rows).sort_values(["invoice_date", "po_number"]).reset_index(drop=True)
    inv.insert(0, "invoice_id", (5105600001 + inv.index).astype(str))   # číslo dokladu faktury
    return inv


# =============================================================================
# KROK 5 – SPOTŘEBA (MSEG, pohyb 261) A ZÁSOBY (MARD, měsíční snímek)
# =============================================================================

def gen_consumption_and_stock(cfg, grs, demand, initial_stock):
    """Spotřeba = denní poptávka z kroku 3 sečtená po týdnech (zaúčtováno v neděli).
    Zásoba = počáteční zásoba + příjmy − spotřeba. Snímek obsahuje jen množství (jako MARD v SAP);
    hodnota = množství × standardní cena se počítá až v Power BI."""
    start, end = pd.Timestamp(cfg["period"]["start"]), pd.Timestamp(cfg["period"]["end"])
    plant = cfg["company"]["plant"]
    days = pd.date_range(start, end, freq="D")
    snapshot_dates = [start - pd.Timedelta(days=1)] + list(pd.date_range(start, end, freq="ME"))
    gr_daily = grs.groupby(["material_id", "posting_date"])["quantity"].sum()
    cons_rows, stock_rows, opening = [], [], {}

    for mid, d in demand.items():
        # 1) Týdenní spotřeba: týden Po–Ne, zaúčtováno v neděli (poslední týden nejpozději ke konci období)
        weekly = pd.Series(d, index=days).groupby(days.to_period("W").end_time.normalize()).sum().round()
        weekly.index = weekly.index.map(lambda x: min(x, end))
        weekly = weekly[weekly > 0]

        # 2) Denní pohyby: + příjmy, − spotřeba
        flows = pd.Series(0.0, index=days)
        if mid in gr_daily.index.get_level_values(0):
            flows = flows.add(gr_daily.loc[mid], fill_value=0)
        flows = flows.sub(weekly, fill_value=0)

        # 3) Zásoba nesmí být záporná → případný deficit dorovnáme do počáteční zásoby
        init = float(np.ceil(initial_stock[mid]))
        balance = init + flows.cumsum()
        if balance.min() < 0:
            init += float(np.ceil(-balance.min()))
            balance = init + flows.cumsum()
        opening[mid] = init

        cons_rows += [{"posting_date": dt, "material_id": mid, "plant": plant, "quantity": q, "movement_type": "261"}
                      for dt, q in weekly.items()]
        stock_rows.append({"snapshot_date": snapshot_dates[0], "material_id": mid, "plant": plant, "stock_qty": init})
        stock_rows += [{"snapshot_date": dt, "material_id": mid, "plant": plant, "stock_qty": round(balance[dt], 2)}
                       for dt in snapshot_dates[1:]]

    consumption = pd.DataFrame(cons_rows).sort_values(["posting_date", "material_id"]).reset_index(drop=True)
    stock = pd.DataFrame(stock_rows).sort_values(["snapshot_date", "material_id"]).reset_index(drop=True)
    return consumption, stock


# =============================================================================
# KROK 6 – ZÁMĚRNÉ CHYBY V DATECH (data governance) + EXPORT
# =============================================================================

def make_typo(name, variant):
    """Překlep v názvu dodavatele – typické chyby při ručním zakládání kmenových dat."""
    first, rest = name.split(" ", 1)
    if variant == 0:                                   # prohozená písmena: Norvald → Norvlad
        first = first[:-3] + first[-2] + first[-3] + first[-1]
        return f"{first} {rest}"
    return name.upper().replace(".", "")               # VELKÁ PÍSMENA bez teček: NORVALD PACKAGING SRO


def inject_data_quality_issues(cfg, rng, vendors, std, header, items, grs, invoices, materials, fx):
    """Vloží 4 typy chyb a vrátí i 'klíč správných odpovědí' (co přesně bylo pokaženo)."""
    dq, log = cfg["data_quality"], []
    end = pd.Timestamp(cfg["period"]["end"])
    mat = materials.set_index("material_id")

    # 1) Duplicitní dodavatel: kopie s překlepem, část objednávek (od náhodného data) jde na kopii
    candidates = vendors[~vendors["_is_late_vendor"] & vendors["vendor_id"].isin(header["vendor_id"])]
    for k, (_, v) in enumerate(candidates.sample(dq["duplicate_vendors"], random_state=int(rng.integers(10**9))).iterrows()):
        dup = v.copy()
        dup["vendor_id"] = f"V{10001 + len(vendors)}"
        dup["vendor_name"] = make_typo(v["vendor_name"], k % 2)
        vendors = pd.concat([vendors, dup.to_frame().T], ignore_index=True)
        po_dates = header.loc[header["vendor_id"] == v["vendor_id"], "po_date"].sort_values()
        cutoff = po_dates.iloc[int(len(po_dates) * rng.uniform(0.4, 0.7))]
        moved = (header["vendor_id"] == v["vendor_id"]) & (header["po_date"] >= cutoff)
        header.loc[moved, "vendor_id"] = dup["vendor_id"]
        invoices.loc[invoices["po_number"].isin(header.loc[moved, "po_number"]), "vendor_id"] = dup["vendor_id"]
        log.append({"issue_type": "duplicate_vendor", "table": "lfa1_vendors", "key": dup["vendor_id"],
                    "detail": f"duplicita k {v['vendor_id']} '{v['vendor_name']}' → '{dup['vendor_name']}', "
                              f"{moved.sum()} objednávek od {cutoff:%d.%m.%Y}"})

    # 2) Chybějící standardní cena: 6 materiálů nakupovaných v posledním roce nemá cenu na tento rok
    bought = items.merge(header[["po_number", "po_date"]], on="po_number")
    bought = bought.loc[bought["po_date"].dt.year == end.year, "material_id"].unique()
    bought = [m for m in bought if mat.loc[m, "spend_type"] == "direct"]
    for mid in rng.choice(bought, dq["missing_standard_price"], replace=False):
        std = std[~((std["material_id"] == mid) & (std["valuation_year"] == end.year))]
        log.append({"issue_type": "missing_standard_price", "table": "mbew_valuation", "key": mid,
                    "detail": f"chybí standardní cena pro rok {end.year}"})

    # 3) Nesoulad měrných jednotek: objednáno v BAL (balení po 100 ks), příjem a kmenová data v KS
    ks_items = items[(items["material_id"].map(mat["uom"]) == "KS") & (items["quantity"] >= 1000)]
    for i in rng.choice(ks_items.index, dq["uom_mismatch"], replace=False):
        items.loc[i, ["quantity", "net_price", "order_uom"]] = [items.loc[i, "quantity"] / 100,
                                                                round(items.loc[i, "net_price"] * 100, 6), "BAL"]
        log.append({"issue_type": "uom_mismatch", "table": "ekpo_po_items",
                    "key": f"{items.loc[i, 'po_number']}/{items.loc[i, 'po_item']}",
                    "detail": f"objednáno v BAL (1 BAL = 100 KS), materiál {items.loc[i, 'material_id']} má jednotku KS"})

    # 4) Faktura bez příjmu: dodavatel fakturuje položku podruhé / bez dodání → selže 3-way match
    rates = rate_lookup(fx)
    received = items.merge(header[["po_number", "po_date", "vendor_id", "currency"]], on="po_number")
    received = received[received["order_uom"] != "BAL"]
    new_rows = []
    for _, r in received.loc[rng.choice(received.index, dq["invoices_without_gr"], replace=False)].iterrows():
        inv_date = min(end, r["po_date"] + pd.Timedelta(days=int(rng.integers(10, 60))))
        amount = round(r["quantity"] * r["net_price"], 2)
        new_rows.append({"invoice_date": inv_date, "vendor_id": r["vendor_id"], "po_number": r["po_number"],
                         "po_item": r["po_item"], "gr_id": None, "material_id": r["material_id"],
                         "quantity": r["quantity"], "unit_price": r["net_price"], "amount": amount,
                         "currency": r["currency"],
                         "amount_czk": round(amount * to_czk(rates, r["currency"], inv_date), 2),
                         "payment_block": ""})
        log.append({"issue_type": "invoice_without_gr", "table": "invoices",
                    "key": f"{r['po_number']}/{r['po_item']}", "detail": "faktura bez vazby na příjem"})
    invoices = pd.concat([invoices.drop(columns="invoice_id"), pd.DataFrame(new_rows)], ignore_index=True)
    invoices = invoices.sort_values(["invoice_date", "po_number"]).reset_index(drop=True)
    invoices.insert(0, "invoice_id", (5105600001 + invoices.index).astype(str))   # přečíslování podle data

    return vendors, std, header, items, invoices, pd.DataFrame(log)


def export(cfg, tables, dq_log):
    """Uloží tabulky do CSV (pro Power BI) a do SQLite (pro SQL). Skryté '_' sloupce se neexportují."""
    csv_dir = ROOT / cfg["output"]["csv_dir"]
    csv_dir.mkdir(parents=True, exist_ok=True)
    db_path = ROOT / cfg["output"]["sqlite_path"]
    db_path.unlink(missing_ok=True)                    # vždy čistá databáze → reprodukovatelnost
    con = sqlite3.connect(db_path)
    for name, df in tables.items():
        out = df.drop(columns=[c for c in df.columns if c.startswith("_")]).copy()
        for c in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[c]):
                out[c] = out[c].dt.strftime("%Y-%m-%d")   # datum jako text RRRR-MM-DD
        out.to_csv(csv_dir / f"{name}.csv", index=False, encoding="utf-8")
        out.to_sql(name, con, index=False)
        print(f"  {name:<16} {len(out):>6} řádků")
    con.close()
    dq_log.to_csv(ROOT / "data" / "dq_answer_key.csv", index=False, encoding="utf-8")
    print(f"Uloženo do {csv_dir.relative_to(ROOT)}/ a {db_path.relative_to(ROOT)}; "
          f"klíč chyb v data/dq_answer_key.csv ({len(dq_log)} záznamů)")


# =============================================================================
# KONTROLNÍ VÝPIS
# =============================================================================

def summary_master_data(vendors, materials, fx, std):
    print("=== LFA1 vendors ===")
    print(f"{len(vendors)} dodavatelů, měny: {vendors['currency'].value_counts().to_dict()}")
    late = vendors[vendors["_is_late_vendor"]].iloc[0]
    print(f"Problémový dodavatel (skrytý): {late['vendor_id']} {late['vendor_name']} ({late['currency']})")
    print(vendors.drop(columns=[c for c in vendors if c.startswith("_")]).head(3).to_string(index=False))

    print("\n=== MARA materials ===")
    by_cat = materials.groupby("category").agg(
        materials=("material_id", "count"),
        spend_mil_czk=("_annual_spend_czk", lambda s: round(s.sum() / 1e6, 1)),
    ).sort_values("spend_mil_czk", ascending=False)
    print(by_cat.to_string())
    print(materials[["material_id", "description", "category", "material_type", "uom", "moq", "lead_time_days"]]
          .sample(4, random_state=1).to_string(index=False))
    late_share = materials.loc[materials["_vendor_id"] == late["vendor_id"], "_annual_spend_czk"].sum() \
        / materials.loc[materials["category"] == "Folding cartons", "_annual_spend_czk"].sum()
    print(f"Podíl problémového dodavatele na spendu kartonů: {late_share:.0%}")

    print("\n=== TCURR fx rates (EUR) ===")
    eur = fx[fx["from_currency"] == "EUR"]
    print(f"EUR/CZK min {eur['actual_rate'].min():.2f}, max {eur['actual_rate'].max():.2f}; "
          f"plánovací kurz po letech: {eur.groupby(eur['month'].dt.year)['plan_rate'].first().to_dict()}")

    print("\n=== MBEW standard prices – průměrný index vs. 2024 ===")
    cat_of = materials.set_index("material_id")["category"]
    s = std.assign(category=std["material_id"].map(cat_of))
    pivot = s.pivot_table(index="category", columns="valuation_year", values="standard_price_czk", aggfunc="sum")
    print((pivot.div(pivot[pivot.columns[0]], axis=0)).round(3).to_string())


def summary_purchase_orders(header, items, materials, fx, std):
    print("\n=== EKKO/EKPO purchase orders ===")
    print(f"{len(header)} objednávek, {len(items)} položek, průměrně {len(items) / len(header):.2f} položky na objednávku")

    df = items.merge(header, on="po_number").merge(
        materials[["material_id", "category", "spend_type", "_behaviour", "_eoq"]], on="material_id")
    df["month"] = df["po_date"].dt.to_period("M").dt.to_timestamp()
    rates = fx.rename(columns={"from_currency": "currency"})[["month", "currency", "actual_rate", "plan_rate"]]
    df = df.merge(rates, on=["month", "currency"], how="left").fillna({"actual_rate": 1.0, "plan_rate": 1.0})
    df["value_czk"] = df["net_value"] * df["actual_rate"]

    print("\nPočet položek po čtvrtletích (sezónnost – špička Q3–Q4):")
    q = df.groupby([df["po_date"].dt.year.rename("year"), df["po_date"].dt.quarter.rename("Q")]).size().unstack()
    print(q.to_string())
    print("\nSpend v mil. Kč podle roku a typu:")
    print((df.pivot_table(index=df["po_date"].dt.year.rename("year"), columns="spend_type",
                          values="value_czk", aggfunc="sum") / 1e6).round(1).to_string())

    # Náhled PPV (z objednávek, ještě ne z příjmů): cena v Kč vs. standardní cena téhož roku
    d = df[df["spend_type"] == "direct"].assign(valuation_year=df["po_date"].dt.year)
    d = d.merge(std[["material_id", "valuation_year", "standard_price_czk"]], on=["material_id", "valuation_year"])
    d["std_value"] = d["standard_price_czk"] * d["quantity"]
    d["ppv"] = d["value_czk"] - d["std_value"]
    d["fx_effect"] = d["net_value"] * (d["actual_rate"] - d["plan_rate"])
    d["group"] = np.where(d["category"] == "Folding cartons", "Folding cartons", "other direct")
    g = d.groupby(["group", "valuation_year"])[["ppv", "fx_effect", "std_value"]].sum()
    g["PPV %"] = (g["ppv"] / g["std_value"] * 100).round(1)
    g["z toho kurz %"] = (g["fx_effect"] / g["std_value"] * 100).round(1)
    print("\nNáhled PPV (kladné = dražší než standard):")
    print(g[["PPV %", "z toho kurz %"]].to_string())

    print("\nPrůměrná dávka / EOQ podle chování materiálu:")
    print(d.assign(ratio=d["quantity"] / d["_eoq"]).groupby("_behaviour")["ratio"].median().round(2).to_string())


def summary_receipts_invoices(header, items, grs, invoices, vendors):
    print("\n=== Goods receipts (101) ===")
    first = grs.groupby(["po_number", "po_item"]).agg(first_gr=("posting_date", "min"),
                                                      first_qty=("quantity", "first"),
                                                      received=("quantity", "sum"))
    df = items.merge(header[["po_number", "vendor_id"]], on="po_number") \
              .merge(first, left_on=["po_number", "po_item"], right_index=True, how="left")
    open_items = df["first_gr"].isna().sum()
    df = df.dropna(subset=["first_gr"])
    # OTIF: první dodávka přišla nejpozději v požadovaný den (on time) A v plném množství (in full)
    df["on_time"] = df["first_gr"] <= df["requested_delivery_date"]
    df["in_full"] = df["first_qty"] >= df["quantity"]
    df["otif"] = df["on_time"] & df["in_full"]
    df["delay_days"] = (df["first_gr"] - df["requested_delivery_date"]).dt.days.clip(lower=0)
    late_ids = set(vendors.loc[vendors["_is_late_vendor"], "vendor_id"])
    df["group"] = np.where(df["vendor_id"].isin(late_ids), "problémový dodavatel", "ostatní")
    print(f"{len(grs)} příjmů, {open_items} položek objednávek zatím nepřijato (otevřené)")
    g = df.groupby("group").agg(položek=("otif", "size"), on_time=("on_time", "mean"), in_full=("in_full", "mean"),
                                OTIF=("otif", "mean"),
                                prům_zpoždění_pozdních=("delay_days", lambda s: s[s > 0].mean()))
    print(g.round(3).to_string())

    print("\n=== Invoices (MIRO) ===")
    po_price = items.set_index(["po_number", "po_item"])["net_price"]
    inv = invoices.join(po_price, on=["po_number", "po_item"])
    inv["diff_pct"] = inv["unit_price"] / inv["net_price"] - 1
    dev = inv[inv["diff_pct"].abs() > 1e-9]
    ipv_czk = (dev["amount_czk"] * (1 - 1 / (1 + dev["diff_pct"]))).sum()
    print(f"{len(invoices)} faktur, s cenovou odchylkou {len(dev)} ({len(dev) / len(invoices):.1%}), "
          f"z toho blokovaných k platbě {(invoices['payment_block'] == 'R').sum()}")
    print(f"IPV celkem ≈ {ipv_czk / 1e3:,.0f} tis. Kč (kladné = dodavatelé účtovali víc)")


def summary_stock(cfg, grs, consumption, stock, std, materials):
    print("\n=== Consumption (261) & stock snapshot (MARD) ===")
    print(f"{len(consumption)} řádků spotřeby, {len(stock)} řádků snímků zásob, "
          f"záporných stavů: {(stock['stock_qty'] < 0).sum()}")

    # Kontrola bilance: konečný stav = počáteční + příjmy − spotřeba (pro každý materiál)
    first_date, last_date = stock["snapshot_date"].min(), stock["snapshot_date"].max()
    s = stock.pivot(index="material_id", columns="snapshot_date", values="stock_qty")
    gr_sum = grs.groupby("material_id")["quantity"].sum().reindex(s.index, fill_value=0)
    cons_sum = consumption.groupby("material_id")["quantity"].sum().reindex(s.index, fill_value=0)
    diff = (s[first_date] + gr_sum - cons_sum - s[last_date]).abs().max()
    print(f"Bilance počáteční + příjmy − spotřeba = konečný stav: max. rozdíl {diff:.4f}")

    # Ukazatele ke konci období
    end = pd.Timestamp(cfg["period"]["end"])
    price = std[std["valuation_year"] == end.year].set_index("material_id")["standard_price_czk"]
    last = s[last_date].to_frame("qty")
    last["value"] = last["qty"] * price.reindex(last.index)
    recent = consumption[consumption["posting_date"] > end - pd.Timedelta(days=90)]
    last["daily_use"] = recent.groupby("material_id")["quantity"].sum().reindex(last.index, fill_value=0) / 90
    last["coverage_days"] = last["qty"] / last["daily_use"].replace(0, np.nan)
    moves = pd.concat([grs[["material_id", "posting_date"]], consumption[["material_id", "posting_date"]]])
    last["days_no_move"] = (end - moves.groupby("material_id")["posting_date"].max()).dt.days.reindex(last.index)
    last = last.join(materials.set_index("material_id")["_behaviour"])

    slow = last[(last["days_no_move"] >= 180) & (last["qty"] > 0)]
    over = last[last["coverage_days"] > 120]
    print(f"Hodnota zásob k {last_date:%d.%m.%Y}: {last['value'].sum() / 1e6:.1f} mil. Kč")
    print(f"Ležáky (180+ dní bez pohybu): {len(slow)} materiálů = {len(slow) / len(last):.0%}, "
          f"hodnota {slow['value'].sum() / 1e6:.1f} mil. Kč")
    print(f"Nadměrná zásoba (pokrytí > 120 dní): {len(over)} materiálů, "
          f"z toho zasazených 'overstock': {(over['_behaviour'] == 'overstock').sum()}")
    normal = last[last["_behaviour"] == "normal"]["coverage_days"]
    print(f"Pokrytí u běžných materiálů: medián {normal.median():.0f} dní")

    # Obrátka zásob = roční spotřeba v Kč / průměrná hodnota zásoby (posledních 12 měsíců)
    year_ago = end - pd.Timedelta(days=365)
    cons12 = consumption[consumption["posting_date"] > year_ago].groupby("material_id")["quantity"].sum()
    cons_value = (cons12 * price.reindex(cons12.index)).sum()
    avg_stock = (s[[c for c in s.columns if c > year_ago]].mul(price.reindex(s.index), axis=0)).sum().mean()
    print(f"Obrátka zásob (12 měsíců): {cons_value / avg_stock:.1f}× ročně "
          f"(≈ {365 / (cons_value / avg_stock):.0f} dní zásoby)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")       # české znaky ve výpisu na Windows
    cfg = load_config()
    rng = np.random.default_rng(cfg["seed"])       # jeden generátor náhody pro celý skript

    # Krok 2 – kmenová data
    vendors = gen_vendors(cfg, rng)
    materials = gen_materials(cfg, rng, vendors)
    fx = gen_fx_rates(cfg, rng)
    std = gen_standard_prices(cfg, rng, materials)
    summary_master_data(vendors, materials, fx, std)

    # Krok 3 – objednávky
    materials = assign_behaviour(cfg, rng, materials)
    po_header, po_items, demand, initial_stock = gen_purchase_orders(cfg, rng, materials, vendors, fx)
    summary_purchase_orders(po_header, po_items, materials, fx, std)

    # Krok 4 – příjmy a faktury
    grs = gen_goods_receipts(cfg, rng, po_header, po_items, vendors, fx)
    invoices = gen_invoices(cfg, rng, grs, po_header, fx)
    summary_receipts_invoices(po_header, po_items, grs, invoices, vendors)

    # Krok 5 – spotřeba a zásoby
    consumption, stock = gen_consumption_and_stock(cfg, grs, demand, initial_stock)
    summary_stock(cfg, grs, consumption, stock, std, materials)

    # Krok 6 – chyby v datech a export
    vendors, std, po_header, po_items, invoices, dq_log = inject_data_quality_issues(
        cfg, rng, vendors, std, po_header, po_items, grs, invoices, materials, fx)
    print("\n=== Export ===")
    export(cfg, {
        "lfa1_vendors": vendors, "mara_materials": materials, "mbew_valuation": std, "fx_rates": fx,
        "ekko_po_header": po_header, "ekpo_po_items": po_items, "goods_receipts": grs,
        "invoices": invoices, "consumption": consumption, "stock_snapshot": stock,
    }, dq_log)


if __name__ == "__main__":
    main()

"""
Kontrola, že "zasazené" vzorce v datech opravdu jsou – čistě přes SQL nad data/mbc.db.

Spuštění (z kořene projektu):
    python generator/check_patterns.py

Každý dotaz je okomentovaný – je to zároveň první ukázka SQL, ze které budeme vycházet
v sql/exercises.md.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DB = Path(__file__).resolve().parents[1] / "data" / "mbc.db"

CHECKS = {
    # -------------------------------------------------------------------------
    "1) Sezónnost – počet položek objednávek podle kalendářního měsíce (špička Q3–Q4)": """
        SELECT CAST(strftime('%m', h.po_date) AS INTEGER) AS month,   -- měsíc z data objednávky
               COUNT(*)                                   AS po_items
        FROM ekpo_po_items i
        JOIN ekko_po_header h ON h.po_number = i.po_number            -- položka → hlavička
        GROUP BY month
        ORDER BY month
    """,
    # -------------------------------------------------------------------------
    "2) PPV podle roku – kartony vs. ostatní přímý materiál (kladné = dražší než standard)": """
        -- PPV = hodnota příjmu v Kč − standardní cena × přijaté množství
        SELECT strftime('%Y', g.posting_date)                          AS year,
               CASE WHEN m.category = 'Folding cartons'
                    THEN 'Folding cartons' ELSE 'other direct' END     AS grp,
               ROUND(SUM(g.amount_czk - v.standard_price_czk * g.quantity) / 1e6, 2) AS ppv_mil_czk,
               ROUND(100.0 * SUM(g.amount_czk - v.standard_price_czk * g.quantity)
                           / SUM(v.standard_price_czk * g.quantity), 1)            AS ppv_pct
        FROM goods_receipts g
        JOIN mara_materials m ON m.material_id = g.material_id
        JOIN mbew_valuation v ON v.material_id = g.material_id         -- standardní cena…
                             AND v.valuation_year = CAST(strftime('%Y', g.posting_date) AS INTEGER)  -- …téhož roku
        WHERE m.spend_type = 'direct'
        GROUP BY year, grp
        ORDER BY grp, year
    """,
    # -------------------------------------------------------------------------
    "3) OTIF – 5 nejhorších dodavatelů (min. 30 položek)": """
        WITH first_gr AS (              -- první příjem ke každé položce objednávky
            SELECT po_number, po_item, MIN(posting_date) AS first_date
            FROM goods_receipts
            GROUP BY po_number, po_item
        ),
        lines AS (                      -- položka + datum a množství první dodávky
            SELECT h.vendor_id,
                   f.first_date <= i.requested_delivery_date AS on_time,
                   g.quantity  >= i.quantity                 AS in_full
            FROM ekpo_po_items i
            JOIN ekko_po_header h ON h.po_number = i.po_number
            JOIN first_gr f       ON f.po_number = i.po_number AND f.po_item = i.po_item
            JOIN goods_receipts g ON g.po_number = i.po_number AND g.po_item = i.po_item
                                 AND g.posting_date = f.first_date
            JOIN mara_materials m ON m.material_id = i.material_id
            WHERE i.order_uom = m.uom   -- položky s chybnou jednotkou vynecháme (viz kontrola 6)
        )
        SELECT l.vendor_id, v.vendor_name, COUNT(*) AS items,
               ROUND(100.0 * AVG(on_time AND in_full), 1) AS otif_pct
        FROM lines l
        JOIN lfa1_vendors v ON v.vendor_id = l.vendor_id
        GROUP BY l.vendor_id
        HAVING COUNT(*) >= 30
        ORDER BY otif_pct
        LIMIT 5
    """,
    # -------------------------------------------------------------------------
    "4) Ležáky – materiály se zásobou a bez pohybu 180+ dní": """
        WITH last_move AS (             -- poslední pohyb = příjem nebo spotřeba
            SELECT material_id, MAX(posting_date) AS last_date
            FROM (SELECT material_id, posting_date FROM goods_receipts
                  UNION ALL
                  SELECT material_id, posting_date FROM consumption)
            GROUP BY material_id
        )
        SELECT COUNT(*) AS slow_movers,
               ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM mara_materials WHERE spend_type = 'direct'), 1)
                   AS pct_of_stock_materials
        FROM stock_snapshot s
        JOIN last_move l ON l.material_id = s.material_id
        WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM stock_snapshot)
          AND s.stock_qty > 0
          AND julianday(s.snapshot_date) - julianday(l.last_date) >= 180
    """,
    # -------------------------------------------------------------------------
    "5) IPV – faktury s jinou cenou než objednávka": """
        SELECT COUNT(*)                                          AS invoices_with_price_diff,
               SUM(inv.payment_block = 'R')                      AS blocked_for_payment,
               ROUND(SUM((inv.unit_price - i.net_price) * inv.quantity
                         * inv.amount_czk / inv.amount) / 1e3, 0) AS ipv_thousand_czk   -- převod do Kč
        FROM invoices inv
        JOIN ekpo_po_items i  ON i.po_number = inv.po_number AND i.po_item = inv.po_item
        JOIN mara_materials m ON m.material_id = i.material_id
        WHERE inv.unit_price <> i.net_price
          AND i.order_uom = m.uom       -- bez položek s chybnou jednotkou
    """,
    # -------------------------------------------------------------------------
    "6) Kvalita dat – počet nalezených problémů podle typu": """
        SELECT 'duplicate_vendor (stejný název po normalizaci 4 znaků)' AS issue,
               COUNT(*) AS found
        FROM lfa1_vendors a
        JOIN lfa1_vendors b ON a.vendor_id < b.vendor_id
                           AND UPPER(SUBSTR(a.vendor_name, 1, 4)) = UPPER(SUBSTR(b.vendor_name, 1, 4))
                           AND a.country = b.country
        UNION ALL
        SELECT 'missing_standard_price (nákup 2026 bez ceny 2026)', COUNT(DISTINCT i.material_id)
        FROM ekpo_po_items i
        JOIN ekko_po_header h ON h.po_number = i.po_number
        JOIN mara_materials m ON m.material_id = i.material_id AND m.spend_type = 'direct'
        LEFT JOIN mbew_valuation v ON v.material_id = i.material_id AND v.valuation_year = 2026
        WHERE strftime('%Y', h.po_date) = '2026' AND v.material_id IS NULL
        UNION ALL
        SELECT 'uom_mismatch (jednotka objednávky ≠ jednotka materiálu)', COUNT(*)
        FROM ekpo_po_items i
        JOIN mara_materials m ON m.material_id = i.material_id
        WHERE i.order_uom <> m.uom
        UNION ALL
        SELECT 'invoice_without_gr (faktura bez příjmu)', COUNT(*)
        FROM invoices
        WHERE gr_id IS NULL
    """,
}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(DB)
    for title, sql in CHECKS.items():
        print(f"\n=== {title} ===")
        print(pd.read_sql(sql, con).to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()

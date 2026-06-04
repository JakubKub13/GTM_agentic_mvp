"""Scout beat definitions."""

from typing import Literal

# The four signal beats; the key matches Signal.signal_type's Literal type.
BeatKey = Literal["erp_migration", "hiring", "ma_leadership", "pain"]

BEATS: list[tuple[BeatKey, str]] = [
    (
        "erp_migration",
        "ERP / supply-chain / finance software migrations and implementations "
        "(SAP, S/4HANA, Oracle, new procurement or reconciliation systems)",
    ),
    (
        "hiring",
        "hiring in supply chain, procurement, operations, or finance "
        "(new directors/managers, team build-outs)",
    ),
    (
        "ma_leadership",
        "M&A activity and leadership changes (new CFO, COO, Supply Chain Director, "
        "acquisitions, mergers)",
    ),
    (
        "pain",
        "operational pain: manual reconciliation, invoice/PO matching, supplier-portal "
        "chaos, inventory or back-office inefficiency",
    ),
]

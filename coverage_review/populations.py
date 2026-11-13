"""人口与响应记录的稳定标识。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HouseholdReceipt:
    household_id: str
    village_id: str
    eligible: bool | None
    acknowledged: bool | None


def observed(receipt: HouseholdReceipt) -> bool:
    return receipt.eligible is not None and receipt.acknowledged is not None

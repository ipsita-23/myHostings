from dataclasses import dataclass, field
from typing import Optional


class AnomalyType:
    DUPLICATE = "DUPLICATE"
    FORMAT = "FORMAT"
    NAME_NORM = "NAME_NORM"
    SETTLEMENT = "SETTLEMENT"
    INVALID_SPLIT = "INVALID_SPLIT"
    CURRENCY = "CURRENCY"
    NON_MEMBER = "NON_MEMBER"
    CONFLICT = "CONFLICT"
    REFUND = "REFUND"
    DATE_FMT = "DATE_FMT"
    MISSING_FIELD = "MISSING_FIELD"
    ZERO_AMOUNT = "ZERO_AMOUNT"
    MEMBERSHIP_DATE = "MEMBERSHIP_DATE"


@dataclass
class Anomaly:
    row_number: int
    raw_row: dict
    anomaly_type: str
    description: str
    action_taken: str           # AUTO_FIXED / SKIPPED / IMPORTED / FLAGGED
    requires_approval: bool = False
    corrected_row: Optional[dict] = field(default=None)

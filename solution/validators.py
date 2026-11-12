from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def normalize_header(raw: str) -> str:
    """Lower-case, trim, and collapse runs of '_' or whitespace into a single space."""
    cleaned = re.sub(r"[_\s]+", " ", raw.strip().lower())
    return cleaned.strip()


def normalize_phone(raw: str | None) -> str | None:
    """
    Normalize Indian mobile number to +91XXXXXXXXXX or return None if invalid.
    Rules:
    - Strip every non-digit
    - If 12 digits starting with 91, drop 91
    - If 11 digits starting with 0, drop 0
    - Valid iff 10 digits starting with 6, 7, 8, or 9
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in {"6", "7", "8", "9"}:
        return f"+91{digits}"
    return None


def parse_date(raw: str) -> str | None:
    """
    Parse day-first date in formats:
    - YYYY-MM-DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - DD-Mon-YYYY (case-insensitive month name)
    Returns canonical YYYY-MM-DD or None if invalid/impossible date.
    """
    val = raw.strip()
    if not val:
        return None

    # YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", val)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # DD/MM/YYYY or DD-MM-YYYY
    m = re.fullmatch(r"(\d{2})[/-](\d{2})[/-](\d{4})", val)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # DD-Mon-YYYY
    m = re.fullmatch(r"(\d{2})-([A-Za-z]{3})-(\d{4})", val)
    if m:
        d, mon_str, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mo = MONTH_MAP.get(mon_str)
        if not mo:
            return None
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def parse_decimal(raw: str) -> str | None:
    """
    Parse currency-prefixed decimal (>= 0), rounded to 2 decimal places.
    Prefixes: ₹, Rs, Rs., INR (case-insensitive, optional space).
    Commas are thousands separators.
    """
    val = raw.strip()
    if not val:
        return None
    val = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", val, flags=re.IGNORECASE).strip()
    val = val.replace(",", "")
    if not val:
        return None
    try:
        dec = Decimal(val)
    except InvalidOperation:
        return None
    if not dec.is_finite() or dec < Decimal("0"):
        return None
    rounded = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def parse_enum(raw: str, allowed_values: list[str]) -> str | None:
    val = raw.strip().lower()
    if not val:
        return None
    allowed_lower = {v.lower() for v in allowed_values}
    return val if val in allowed_lower else None


def validate_field_value(raw: str, field_spec: dict[str, Any]) -> tuple[bool, Any]:
    """
    Validate and normalize a non-empty trimmed string value against field_spec.
    Returns (is_valid, normalized_value).
    """
    ftype = field_spec["type"]
    if ftype == "string":
        return True, raw
    if ftype == "phone":
        norm = normalize_phone(raw)
        return (norm is not None), norm
    if ftype == "date":
        norm = parse_date(raw)
        return (norm is not None), norm
    if ftype == "decimal":
        norm = parse_decimal(raw)
        return (norm is not None), norm
    if ftype == "enum":
        norm = parse_enum(raw, field_spec.get("values", []))
        return (norm is not None), norm
    return True, raw


def parse_timestamp_ist(raw: str | None) -> datetime | None:
    """
    Parse ISO/naive timestamp string and convert to IST (+05:30).
    Naive strings are assumed to already be in IST (+05:30).
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt

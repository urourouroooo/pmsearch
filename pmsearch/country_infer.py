from __future__ import annotations

import re

import pycountry

# 常见别名（机构地址末尾写法；lookup 无法识别的）
_ALIASES: dict[str, str] = {
    "usa": "United States",
    "u.s.a": "United States",
    "u.s.a.": "United States",
    "uk": "United Kingdom",
    "u.k": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "pr china": "China",
    "p.r. china": "China",
    "people's republic of china": "China",
    "south korea": "Korea, Republic of",
    "republic of korea": "Korea, Republic of",
    "the netherlands": "Netherlands",
    "czech republic": "Czechia",
    "russia": "Russian Federation",
}

_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
    .split()
)

_US_STATE_NAMES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
}


def _norm_segment(seg: str) -> str:
    s = seg.strip().rstrip(".").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _lookup_country(seg: str) -> str:
    """将单段地址解析为英文国名；失败返回空串。"""
    raw = _norm_segment(seg)
    if not raw:
        return ""
    tl = raw.lower()
    if tl in _ALIASES:
        return _ALIASES[tl]
    try:
        return str(pycountry.countries.lookup(raw).name)
    except Exception:
        pass
    try:
        return str(pycountry.countries.lookup(tl).name)
    except Exception:
        pass
    return ""


def _infer_us_from_tail(parts: list[str]) -> str:
    if len(parts) < 2:
        return ""
    last = _norm_segment(parts[-1])
    prev = _norm_segment(parts[-2])
    last_core = re.sub(r"^\d{4,}\s*", "", last)
    token = last_core.split()[0] if last_core else ""
    if len(token) == 2 and token.upper() in _US_STATE_CODES:
        return "United States"
    if last_core.lower() in _US_STATE_NAMES:
        return "United States"
    if prev.lower() in _US_STATE_NAMES:
        return "United States"
    if len(prev) == 2 and prev.upper() in _US_STATE_CODES:
        return "United States"
    return ""


def infer_country_from_affiliation(affiliation: str) -> str:
    """
    从通讯作者机构字符串推断国家（pycountry 英文国名）。
    多段 affiliation 用分号分隔时取第一段。
    """
    if not affiliation or not affiliation.strip():
        return ""
    first_block = affiliation.split(";")[0].strip()
    parts = [_norm_segment(p) for p in first_block.split(",") if _norm_segment(p)]
    if not parts:
        return ""

    for seg in reversed(parts[-4:]):
        hit = _lookup_country(seg)
        if hit:
            return hit

    us = _infer_us_from_tail(parts)
    if us:
        return us

    blob = first_block.lower()
    for c in sorted(pycountry.countries, key=lambda x: len(x.name), reverse=True):
        if len(c.name) < 5:
            continue
        if c.name.lower() in blob:
            return str(c.name)
    return ""

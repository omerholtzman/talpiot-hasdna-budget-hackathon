# -*- coding: utf-8 -*-
"""Static reference data for the Israeli state budget (ספר התקציב).

Generated from budget_items_data on 2026-07-31 and checked in deliberately: the
pipeline needs the office list *before* it queries anything, and the term-expansion
prompt must choose from a closed set so the model cannot invent a budget code.
Regenerate with tools/refresh_reference.py when a new budget year lands.
"""

from typing import Dict, List, Optional

LATEST_YEAR = 2026
FIRST_YEAR = 1997

# code -> (title in its most recent year, first year seen, last year seen)
OFFICES: Dict[str, Dict] = {
    "01": {"title": "נשיא המדינה ולשכתו", "first_year": 1997, "last_year": 2026},
    "02": {"title": "הכנסת", "first_year": 1997, "last_year": 2026},
    "03": {"title": "חברי ממשלה", "first_year": 1997, "last_year": 2012},
    "04": {"title": "משרד ראש הממשלה", "first_year": 1997, "last_year": 2026},
    "05": {"title": "משרד האוצר", "first_year": 1997, "last_year": 2026},
    "06": {"title": "משרד הפנים", "first_year": 1997, "last_year": 2026},
    "07": {"title": "המשרד לביטחון לאומי", "first_year": 1997, "last_year": 2026},
    "08": {"title": "משרד המשפטים", "first_year": 1997, "last_year": 2026},
    "09": {"title": "משרד החוץ", "first_year": 1997, "last_year": 2026},
    "10": {"title": "מטה לביטחון לאומי", "first_year": 2009, "last_year": 2026},
    "11": {"title": "מבקר המדינה", "first_year": 1997, "last_year": 2026},
    "12": {"title": "גמלאות ופיצויים", "first_year": 1997, "last_year": 2026},
    "13": {"title": "הוצאות שונות", "first_year": 1997, "last_year": 2026},
    "14": {"title": "בחירות ומימון מפלגות", "first_year": 1997, "last_year": 2026},
    "15": {"title": "משרד הביטחון", "first_year": 1997, "last_year": 2026},
    "16": {"title": "הוצאות חירום אזרחיות", "first_year": 1997, "last_year": 2026},
    "17": {"title": "תאום הפעולות בשטחים", "first_year": 1997, "last_year": 2026},
    "18": {"title": "הרשויות המקומיות", "first_year": 1997, "last_year": 2026},
    "19": {"title": "מדע, תרבות וספורט", "first_year": 1997, "last_year": 2026},
    "20": {"title": "משרד החינוך", "first_year": 1997, "last_year": 2026},
    "21": {"title": "ההשכלה הגבוהה", "first_year": 1997, "last_year": 2026},
    "22": {"title": "המשרד לשירותי דת", "first_year": 1997, "last_year": 2026},
    "23": {"title": "משרד הרווחה", "first_year": 1997, "last_year": 2026},
    "24": {"title": "משרד הבריאות", "first_year": 1997, "last_year": 2026},
    "25": {"title": "הרשות לניצולי השואה", "first_year": 1997, "last_year": 2026},
    "26": {"title": "המשרד להגנת הסביבה", "first_year": 1997, "last_year": 2026},
    "27": {"title": "הקצבות לביטוח לאומי", "first_year": 1997, "last_year": 2026},
    "28": {"title": "אחזקת כבישים", "first_year": 1997, "last_year": 2004},
    "29": {"title": "משרד הבינוי והשיכון", "first_year": 1997, "last_year": 2026},
    "30": {"title": "משרד העלייה והקליטה", "first_year": 1997, "last_year": 2026},
    "31": {"title": "הוצאות ביטחוניות שונות", "first_year": 1997, "last_year": 2026},
    "32": {"title": "תמיכות שונות", "first_year": 1997, "last_year": 2018},
    "33": {"title": "משרד החקלאות", "first_year": 1997, "last_year": 2026},
    "34": {"title": "משרד האנרגיה", "first_year": 1997, "last_year": 2026},
    "35": {"title": "הועדה לאנרגיה אטומית", "first_year": 1997, "last_year": 2026},
    "36": {"title": "תעסוקה", "first_year": 1997, "last_year": 2026},
    "37": {"title": "משרד התיירות", "first_year": 1997, "last_year": 2026},
    "38": {"title": "כלכלה ותעשייה", "first_year": 1997, "last_year": 2026},
    "39": {"title": "משרד התקשורת", "first_year": 1997, "last_year": 2026},
    "40": {"title": "משרד התחבורה", "first_year": 1997, "last_year": 2026},
    "41": {"title": "רשות ממשלתית למים וביוב", "first_year": 1997, "last_year": 2026},
    "42": {"title": "מענקי בינוי ושיכון", "first_year": 1997, "last_year": 2026},
    "43": {"title": "המרכז למיפוי ישראל", "first_year": 1997, "last_year": 2026},
    "44": {"title": "סבסוד אשראי", "first_year": 1997, "last_year": 2008},
    "45": {"title": "תשלום ריבית ועמלות", "first_year": 1997, "last_year": 2026},
    "46": {"title": "חוק חיילים משוחררים", "first_year": 1997, "last_year": 2026},
    "47": {"title": "רזרבה כללית", "first_year": 1997, "last_year": 2026},
    "48": {"title": "רזרבה להתייקרויות", "first_year": 1997, "last_year": 1999},
    "51": {"title": "דיור ממשלתי", "first_year": 1997, "last_year": 2026},
    "52": {"title": "המשטרה ובתי הסוהר", "first_year": 1997, "last_year": 2026},
    "53": {"title": "משפטים ובתי משפט", "first_year": 1997, "last_year": 2021},
    "54": {"title": "רשויות פיקוח", "first_year": 1997, "last_year": 2026},
    "55": {"title": "אוצר", "first_year": 1997, "last_year": 2012},
    "56": {"title": "נציבות שוויון זכויות", "first_year": 1999, "last_year": 2012},
    "57": {"title": "רשויות מקומיות", "first_year": 1997, "last_year": 2012},
    "58": {"title": "תאגידים עירוניים למים", "first_year": 2003, "last_year": 2008},
    "60": {"title": "חינוך", "first_year": 1997, "last_year": 2026},
    "67": {"title": "בריאות", "first_year": 1997, "last_year": 2026},
    "68": {"title": "רשות האוכלוסין", "first_year": 2003, "last_year": 2026},
    "70": {"title": "שיכון", "first_year": 1997, "last_year": 2026},
    "72": {"title": "חקלאות", "first_year": 1997, "last_year": 2003},
    "73": {"title": "מפעלי מים", "first_year": 1997, "last_year": 2026},
    "76": {"title": "תעשייה", "first_year": 1997, "last_year": 2026},
    "78": {"title": "תיירות", "first_year": 1997, "last_year": 2026},
    "79": {"title": "תחבורה", "first_year": 1997, "last_year": 2026},
    "80": {"title": "כבישים ומסילות ברזל", "first_year": 1997, "last_year": 2000},
    "81": {"title": "פיתוח התקשורת", "first_year": 1997, "last_year": 1999},
    "83": {"title": "הוצאות פיתוח אחרות", "first_year": 1997, "last_year": 2026},
    "84": {"title": "תשלום חובות", "first_year": 1997, "last_year": 2026},
    "89": {"title": "מפעלי משרד ראה\"מ והאוצר", "first_year": 1997, "last_year": 2026},
    "91": {"title": "פיתוח לאומי", "first_year": 1997, "last_year": 2020},
    "92": {"title": "בתי חולים גריאטריים", "first_year": 2026, "last_year": 2026},
    "93": {"title": "בתי חולים לבריאות הנפש", "first_year": 2015, "last_year": 2026},
    "94": {"title": "בתי חולים ממשלתים", "first_year": 1998, "last_year": 2026},
    "95": {"title": "נמל חדרה", "first_year": 1997, "last_year": 2026},
    "96": {"title": "הוצאות בנק הדואר", "first_year": 1997, "last_year": 2006},
    "98": {"title": "רשות מקרקעי ישראל", "first_year": 1997, "last_year": 2026},
}

CURRENT_OFFICES = {c: v for c, v in OFFICES.items() if v["last_year"] >= LATEST_YEAR}
HISTORICAL_OFFICES = {c: v for c, v in OFFICES.items() if v["last_year"] < LATEST_YEAR}

# Every policy area appears twice: an ordinary-budget office and a development one.
# Searching only the low-numbered office is the most common way to under-report a subject.
# Development items are also recognisable by economic_class_primary == "השקעה".
DEVELOPMENT_PAIRS: List[Dict] = [
    {"area": "בריאות", "ordinary": ['24'], "development": ['67', '92', '93', '94']},
    {"area": "חינוך", "ordinary": ['20'], "development": ['60']},
    {"area": "השכלה גבוהה", "ordinary": ['21'], "development": []},
    {"area": "תחבורה", "ordinary": ['40'], "development": ['79']},
    {"area": "בינוי ושיכון", "ordinary": ['29'], "development": ['70', '42', '51']},
    {"area": "כלכלה ותעשייה", "ordinary": ['38'], "development": ['76']},
    {"area": "תיירות", "ordinary": ['37'], "development": ['78']},
    {"area": "מים", "ordinary": ['41'], "development": ['73']},
    {"area": "אנרגיה", "ordinary": ['34', '35'], "development": ['83']},
    {"area": "ביטחון פנים", "ordinary": ['07'], "development": ['52']},
    {"area": "חקלאות", "ordinary": ['33'], "development": []},
    {"area": "הגנת הסביבה", "ordinary": ['26'], "development": []},
    {"area": "רווחה", "ordinary": ['23'], "development": []},
    {"area": "תקשורת", "ordinary": ['39'], "development": []},
    {"area": "ראש הממשלה ואוצר", "ordinary": ['04', '05'], "development": ['89']},
]

# functional_class_detailed is populated for level=4 rows only. Closed set.
FUNCTIONAL_CLASSES: List[str] = [
    "אוצר",
    "אנרגיה",
    "בטחון",
    "בטחון פנים",
    "בטחון-אחר",
    "בינוי ושיכון",
    "בריאות",
    "גמלאות",
    "הגנת הסביבה",
    "הוצאות שונות",
    "העברות לביטוח הלאומי",
    "השכלה גבוהה",
    "חוץ",
    "חינוך",
    "חקלאות",
    "כלכלה ותעשיה",
    "מדע, תרבות וספורט",
    "משפטים",
    "משק המים",
    "משרדים נוספים",
    "פנים ושלטון מקומי",
    "קליטת עליה",
    "קרן",
    "קרן - ביטוח לאומי",
    "ראש הממשלה",
    "רווחה",
    "רזרבה",
    "ריבית",
    "רשות מקרקעי ישראל",
    "שירותי דת",
    "תחבורה",
    "תיירות",
    "תעסוקה",
    "תעשיה ותעסוקה",
    "תקשורת",
]

ECONOMIC_CLASSES: List[str] = [
    "הוצאות הון",
    "החזר חוב - קרן",
    "החזר חוב - ריבית",
    "הכנסות  מיועדות",
    "הכנסות מיועדות",
    "העברות",
    "העברות  פנים תקציביות",
    "השקעה",
    "חשבונות מעבר",
    "מתן אשראי",
    "קניות",
    "רזרבות",
    "שכר",
]

# Real values in the data carry inconsistent internal spacing - both "הכנסות מיועדות"
# and "הכנסות  מיועדות" (two spaces) occur. Always compare via normalize().
_NON_SUBSTANTIVE = ["הכנסות מיועדות", "העברות פנים תקציביות", "חשבונות מעבר", "רזרבות"]


def normalize(text: Optional[str]) -> str:
    """Collapses internal whitespace so class names compare reliably."""
    return " ".join((text or "").split())


def is_non_substantive(economic_class: Optional[str]) -> bool:
    """True for accounting artefacts that belong in related_items, not in totals."""
    return normalize(economic_class) in {normalize(x) for x in _NON_SUBSTANTIVE}


def development_offices_for(office_codes: List[str]) -> List[str]:
    """Given office codes, adds the development twins the caller may have missed.

    Order follows DEVELOPMENT_PAIRS so a given input always yields the same output -
    runs need to be reproducible to be comparable across models.
    """
    have = set(office_codes)
    seen, result = set(), []
    for pair in DEVELOPMENT_PAIRS:
        known = list(pair["ordinary"]) + list(pair["development"])
        if not have.intersection(known):
            continue
        for code in known:
            if code not in have and code not in seen:
                seen.add(code)
                result.append(code)
    return result


def valid_offices(codes: List[str]) -> List[str]:
    """Keeps only codes that really exist as level-1 sections."""
    return [c for c in codes if c in OFFICES]


# Stand: 02.11.2026
"""DuckDuckGo regions and the language/country to region lookup.

The table is read from DuckDuckGo's own settings script (``regions`` in
duckduckgo.com/dist/util/u.*.js, checked 02.11.2026): 64 codes, ``wt-wt`` included.
ddgs passes the code on without checking it and some engines split it blindly
at "-", so a malformed code must never get that far.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

DDG_REGIONS: Dict[str, str] = {
    "wt-wt": "All regions",
    "ar-es": "Argentina",
    "au-en": "Australia",
    "at-de": "Austria",
    "be-fr": "Belgium (fr)",
    "be-nl": "Belgium (nl)",
    "br-pt": "Brazil",
    "bg-bg": "Bulgaria",
    "ca-en": "Canada (en)",
    "ca-fr": "Canada (fr)",
    "ct-ca": "Catalonia",
    "cl-es": "Chile",
    "cn-zh": "China",
    "co-es": "Colombia",
    "hr-hr": "Croatia",
    "cz-cs": "Czech Republic",
    "dk-da": "Denmark",
    "ee-et": "Estonia",
    "fi-fi": "Finland",
    "fr-fr": "France",
    "de-de": "Germany",
    "gr-el": "Greece",
    "hk-tzh": "Hong Kong",
    "hu-hu": "Hungary",
    "is-is": "Iceland",
    "in-en": "India (en)",
    "id-en": "Indonesia (en)",
    "ie-en": "Ireland",
    "il-en": "Israel (en)",
    "it-it": "Italy",
    "jp-jp": "Japan",
    "kr-kr": "Korea",
    "lv-lv": "Latvia",
    "lt-lt": "Lithuania",
    "my-en": "Malaysia (en)",
    "mx-es": "Mexico",
    "nl-nl": "Netherlands",
    "nz-en": "New Zealand",
    "no-no": "Norway",
    "pk-en": "Pakistan (en)",
    "pe-es": "Peru",
    "ph-en": "Philippines (en)",
    "pl-pl": "Poland",
    "pt-pt": "Portugal",
    "ro-ro": "Romania",
    "ru-ru": "Russia",
    "xa-ar": "Saudi Arabia",
    "sg-en": "Singapore",
    "sk-sk": "Slovakia",
    "sl-sl": "Slovenia",
    "za-en": "South Africa",
    "es-ca": "Spain (ca)",
    "es-es": "Spain (es)",
    "se-sv": "Sweden",
    "ch-de": "Switzerland (de)",
    "ch-fr": "Switzerland (fr)",
    "tw-tzh": "Taiwan",
    "th-en": "Thailand (en)",
    "tr-tr": "Turkey",
    "us-en": "US (English)",
    "us-es": "US (Spanish)",
    "ua-uk": "Ukraine",
    "uk-en": "United Kingdom",
    "vn-en": "Vietnam (en)",
}

DEFAULT_REGION = "wt-wt"
_FORMAT = re.compile(r"^[a-z]{2}-[a-z]{2,3}$")

# The region a bare language code stands for. Every value must be in DDG_REGIONS.
LANGUAGE_REGION: Dict[str, str] = {
    "en": "us-en", "de": "de-de", "es": "es-es", "fr": "fr-fr", "nl": "nl-nl",
    "it": "it-it", "pt": "pt-pt", "pl": "pl-pl", "ru": "ru-ru", "tr": "tr-tr",
    "ja": "jp-jp", "ko": "kr-kr", "zh": "cn-zh", "sv": "se-sv", "da": "dk-da",
    "no": "no-no", "nb": "no-no", "fi": "fi-fi", "cs": "cz-cs", "el": "gr-el",
    "hu": "hu-hu", "ro": "ro-ro", "uk": "ua-uk", "ar": "xa-ar", "bg": "bg-bg",
    "hr": "hr-hr", "et": "ee-et", "lv": "lv-lv", "lt": "lt-lt", "sk": "sk-sk",
    "sl": "sl-sl", "is": "is-is", "ca": "ct-ca",
}


def is_region(code: str) -> bool:
    """True for a well-formed code that DuckDuckGo knows."""
    return bool(_FORMAT.match(code)) and code in DDG_REGIONS


def ddg_region(language: Optional[str] = None, country: Optional[str] = None) -> str:
    """DuckDuckGo region for a language and an optional country.

    Tries country+language (``nl`` + ``BE`` gives ``be-nl``), then the language
    alone (``nl`` gives ``nl-nl``), then ``wt-wt``. No language, ``any`` or
    anything unknown gives ``wt-wt``. A ready region code such as ``be-nl`` is
    taken as is.
    """
    lang = (language or "").strip().lower().replace("_", "-")
    if not lang or lang == "any":
        return DEFAULT_REGION
    cc = (country or "").strip().lower()
    if "-" in lang:
        if is_region(lang):
            return lang
        lang, _, tail = lang.partition("-")
        cc = cc or tail
    for code in (f"{cc}-{lang}",):
        if is_region(code):
            return code
    code = LANGUAGE_REGION.get(lang, "")
    return code if is_region(code) else DEFAULT_REGION

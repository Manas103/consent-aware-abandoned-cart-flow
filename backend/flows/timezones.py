"""Country calling code -> one representative IANA timezone.

Deliberately small and approximate: several of these countries (the US,
Canada, Australia, Russia) span multiple zones in reality. A production
system would resolve a real per-number zone (carrier lookup, or an
explicit zone supplied at signup) instead of guessing from the calling
code. This mapping exists so quiet-hours math has *a* real, genuinely
different-from-UTC zone to compute in, and is disclosed as a
simplification in the README rather than hidden.
"""

COUNTRY_CODE_TO_TIMEZONE = {
    "1": "America/New_York",
    "44": "Europe/London",
    "91": "Asia/Kolkata",
    "61": "Australia/Sydney",
    "81": "Asia/Tokyo",
    "49": "Europe/Berlin",
    "33": "Europe/Paris",
    "55": "America/Sao_Paulo",
    "234": "Africa/Lagos",
    "65": "Asia/Singapore",
    "971": "Asia/Dubai",
    "27": "Africa/Johannesburg",
    "52": "America/Mexico_City",
    "86": "Asia/Shanghai",
    "7": "Europe/Moscow",
    "82": "Asia/Seoul",
    "63": "Asia/Manila",
    "234": "Africa/Lagos",
}

DEFAULT_TIMEZONE = "UTC"


def timezone_for_country_code(country_code: str) -> str:
    return COUNTRY_CODE_TO_TIMEZONE.get(country_code.lstrip("+"), DEFAULT_TIMEZONE)

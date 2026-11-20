"""Shared goal keys for athlete preferences and exercise prescriptions."""

from __future__ import annotations


_GOAL_KEY_ALIASES = {
    "general": (
        "general",
        "ggeneral",
        "general_fitness",
        "general fitness",
        "mobility",
        "عمومی",
        "تناسب عمومی",
        "سلامت و آمادگی عمومی",
        "انعطاف و تحرک",
    ),
    "strength": ("strength", "قدرت", "افزایش قدرت"),
    "hypertrophy": (
        "hypertrophy",
        "volume",
        "muscle gain",
        "muscle_gain",
        "حجم",
        "افزایش حجم عضلانی",
    ),
    "fat_burning": (
        "fat_burning",
        "fat burning",
        "fat-burn",
        "fat burn",
        "body_composition",
        "body composition",
        "چربی سوزی",
        "چربی‌سوزی",
        "ترکیب بدنی",
    ),
    "endurance": ("endurance", "استقامت"),
    "power": (
        "power",
        "sport_performance",
        "sport performance",
        "توان",
        "توان انفجاری",
        "عملکرد ورزشی",
    ),
    "cardio": ("cardio", "aerobic", "هوازی"),
}

_NORMALIZED_ALIASES = {
    " ".join(alias.casefold().replace("_", " ").replace("-", " ").split()): key
    for key, aliases in _GOAL_KEY_ALIASES.items()
    for alias in aliases
}

GOAL_LOOKUP_ALIASES = {
    key: tuple(dict.fromkeys(aliases))
    for key, aliases in {
        "general": ("general", "Ggeneral", "general_fitness", "عمومی", "تناسب عمومی"),
        "strength": ("strength", "قدرت"),
        "hypertrophy": ("hypertrophy", "volume", "muscle gain", "حجم"),
        "fat_burning": ("fat_burning", "body_composition", "fat burn", "چربی سوزی", "چربی‌سوزی"),
        "endurance": ("endurance", "استقامت"),
        "power": ("power", "sport_performance", "توان", "توان انفجاری"),
        "cardio": ("cardio", "aerobic", "هوازی"),
        "general_fitness": ("general", "Ggeneral", "general_fitness", "عمومی", "تناسب عمومی"),
        "mobility": ("general", "Ggeneral", "general_fitness", "mobility", "عمومی", "تناسب عمومی"),
        "volume": ("hypertrophy", "volume", "muscle gain", "حجم"),
        "body_composition": ("fat_burning", "body_composition", "fat burn", "چربی سوزی", "چربی‌سوزی"),
        "sport_performance": ("power", "sport_performance", "توان", "توان انفجاری"),
    }.items()
}

PROGRAM_GOAL_TO_TRAINING_GOAL = {
    "general": "general_fitness",
    "strength": "strength",
    "hypertrophy": "hypertrophy",
    "fat_burning": "body_composition",
    "endurance": "endurance",
    "power": "sport_performance",
    "cardio": "general_fitness",
}


def canonical_goal_key(value: object) -> str:
    """Normalize legacy labels and aliases into a stable catalog key."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = " ".join(raw.casefold().replace("_", " ").replace("-", " ").split())
    return _NORMALIZED_ALIASES.get(normalized, normalized.replace(" ", "_"))


def goal_lookup_aliases(value: object) -> tuple[str, ...]:
    """Return legacy database labels that belong to a canonical goal key."""
    key = canonical_goal_key(value)
    return GOAL_LOOKUP_ALIASES.get(key, (str(value or "").strip(),))


def training_goal_for_program_goal(value: object) -> str:
    """Map a prescription catalog key to the training-volume goal category."""
    return PROGRAM_GOAL_TO_TRAINING_GOAL.get(canonical_goal_key(value), "general_fitness")

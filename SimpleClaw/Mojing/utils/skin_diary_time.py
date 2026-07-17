"""Business time-window rules for automatic skin diary generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")

DIARY_SLOT_MORNING = "morning"
DIARY_SLOT_MIDDAY = "midday"
DIARY_SLOT_EVENING = "evening"

GENERATION_REASON_AUTO_MORNING = "auto_morning"
GENERATION_REASON_AUTO_MIDDAY_FALLBACK = "auto_midday_fallback"
GENERATION_REASON_AUTO_EVENING = "auto_evening"


@dataclass(frozen=True)
class SkinDiaryGenerationWindow:
    """Resolved automatic generation window for a skin-profile-sync event."""

    should_consider: bool
    business_date: date | None
    diary_slot: str | None
    generation_reason: str | None
    local_time: datetime


@dataclass(frozen=True)
class SkinDiaryTimeRange:
    """Half-open Beijing-time range [start, end) used for DB lookups."""

    start: datetime
    end: datetime


def to_beijing_time(moment: datetime | None = None) -> datetime:
    """Return ``moment`` converted to Beijing time.

    Naive datetimes are treated as already being in Beijing time. This keeps
    tests and DB-triggered code deterministic while all production decisions use
    a single business timezone.
    """

    if moment is None:
        return datetime.now(BEIJING_TZ)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=BEIJING_TZ)
    return moment.astimezone(BEIJING_TZ)


def resolve_skin_diary_generation_window(
    moment: datetime | None = None,
) -> SkinDiaryGenerationWindow:
    """Resolve the automatic skin diary window for a sync-success moment.

    Rules use Beijing time:
    - 04:00-10:59 -> today's morning diary.
    - 11:00-17:59 -> today's midday fallback diary if the day has no diary.
    - 18:00-23:59 -> today's evening diary.
    - 00:00-02:59 -> previous day's evening diary.
    - 03:00-03:59 -> no automatic generation.
    """

    local = to_beijing_time(moment)
    hour = local.hour

    if 4 <= hour < 11:
        return SkinDiaryGenerationWindow(
            should_consider=True,
            business_date=local.date(),
            diary_slot=DIARY_SLOT_MORNING,
            generation_reason=GENERATION_REASON_AUTO_MORNING,
            local_time=local,
        )
    if 11 <= hour < 18:
        return SkinDiaryGenerationWindow(
            should_consider=True,
            business_date=local.date(),
            diary_slot=DIARY_SLOT_MIDDAY,
            generation_reason=GENERATION_REASON_AUTO_MIDDAY_FALLBACK,
            local_time=local,
        )
    if 18 <= hour < 24:
        return SkinDiaryGenerationWindow(
            should_consider=True,
            business_date=local.date(),
            diary_slot=DIARY_SLOT_EVENING,
            generation_reason=GENERATION_REASON_AUTO_EVENING,
            local_time=local,
        )
    if 0 <= hour < 3:
        return SkinDiaryGenerationWindow(
            should_consider=True,
            business_date=local.date() - timedelta(days=1),
            diary_slot=DIARY_SLOT_EVENING,
            generation_reason=GENERATION_REASON_AUTO_EVENING,
            local_time=local,
        )

    return SkinDiaryGenerationWindow(
        should_consider=False,
        business_date=None,
        diary_slot=None,
        generation_reason=None,
        local_time=local,
    )


def skin_diary_business_day_range(business_date: date) -> SkinDiaryTimeRange:
    """Return the create_time range belonging to a business diary date.

    A business diary date starts at 04:00 and includes the following day's
    00:00-02:59 evening window, ending at 03:00 the next calendar day.
    """

    start = datetime.combine(business_date, time(hour=4), tzinfo=BEIJING_TZ)
    end = datetime.combine(business_date + timedelta(days=1), time(hour=3), tzinfo=BEIJING_TZ)
    return SkinDiaryTimeRange(start=start, end=end)


def skin_diary_slot_range(business_date: date, diary_slot: str) -> SkinDiaryTimeRange:
    """Return the create_time range for a business date and inferred slot."""

    if diary_slot == DIARY_SLOT_MORNING:
        return SkinDiaryTimeRange(
            start=datetime.combine(business_date, time(hour=4), tzinfo=BEIJING_TZ),
            end=datetime.combine(business_date, time(hour=11), tzinfo=BEIJING_TZ),
        )
    if diary_slot == DIARY_SLOT_MIDDAY:
        return SkinDiaryTimeRange(
            start=datetime.combine(business_date, time(hour=11), tzinfo=BEIJING_TZ),
            end=datetime.combine(business_date, time(hour=18), tzinfo=BEIJING_TZ),
        )
    if diary_slot == DIARY_SLOT_EVENING:
        return SkinDiaryTimeRange(
            start=datetime.combine(business_date, time(hour=18), tzinfo=BEIJING_TZ),
            end=datetime.combine(business_date + timedelta(days=1), time(hour=3), tzinfo=BEIJING_TZ),
        )
    raise ValueError(f"unknown skin diary slot: {diary_slot}")


def infer_skin_diary_metadata_from_create_time(
    create_time: datetime | None,
) -> SkinDiaryGenerationWindow:
    """Infer business date and slot from an existing row's create_time."""

    return resolve_skin_diary_generation_window(create_time)


def strip_tz(moment: datetime) -> datetime:
    """Return a naive datetime for MySQL DATETIME comparisons."""

    return moment.replace(tzinfo=None)

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import icalendar
import pytest

from my_school_menus.calendar import (
    build_calendar,
    calendar_name,
    description,
    event_uid,
    feed_filename,
    short_headline,
    staples,
    summary,
)
from my_school_menus.models import (
    DistrictId,
    ItemKind,
    MealType,
    Menu,
    MenuDay,
    MenuId,
    MenuItem,
    Section,
    Site,
    SiteId,
    parse_month,
)

SITE = Site(id=SiteId(12589), district=DistrictId(1265), name="Chambers Primary")
LUNCH = Menu(id=MenuId(125549), name="Primary SY 26/27 Lunch Menu", meal_type=MealType.LUNCH, published_months=())
BREAKFAST = Menu(id=MenuId(125546), name="Breakfast", meal_type=MealType.BREAKFAST, published_months=())
STAMP = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def lunch_days(lunch_month: Any) -> tuple[MenuDay, ...]:
    return parse_month(lunch_month["data"])


def ics_lines(cal: icalendar.Calendar) -> list[str]:
    return cal.to_ical().decode().replace("\r\n ", "").split("\r\n")


def test_event_uid_stable(lunch_days: tuple[MenuDay, ...]) -> None:
    day = lunch_days[0]
    other_site = replace(SITE, id=SiteId(99999))
    other_menu = replace(LUNCH, id=MenuId(1))

    assert event_uid(SITE, LUNCH, day) == event_uid(SITE, LUNCH, day) == "12589-125549-2026-09-03@my-school-menus"
    assert event_uid(SITE, LUNCH, lunch_days[1]) != event_uid(SITE, LUNCH, day)
    assert event_uid(SITE, other_menu, day) != event_uid(SITE, LUNCH, day)
    assert event_uid(other_site, LUNCH, day) != event_uid(SITE, LUNCH, day)


def test_build_calendar_rfc_fields(lunch_days: tuple[MenuDay, ...]) -> None:
    cal = build_calendar(SITE, LUNCH, lunch_days, stamp=STAMP)
    lines = ics_lines(cal)

    assert "VERSION:2.0" in lines
    assert "PRODID:-//andrewdefilippis//my-school-menus//EN" in lines
    assert "X-WR-CALNAME:Chambers Primary Lunch" in lines
    assert "UID:1265-12589-lunch@my-school-menus" in lines
    events = cal.walk("VEVENT")
    assert len(events) == 3
    for event in events:
        assert event["UID"].endswith("@my-school-menus")
        assert event["DTSTAMP"].dt == STAMP
        assert event["TRANSP"] == "TRANSPARENT"
        assert type(event["DTSTART"].dt) is date
    assert lines.count("DTSTART;VALUE=DATE:20260922") == 1

    reparsed = icalendar.Calendar.from_ical(cal.to_ical())
    assert [str(e["UID"]) for e in reparsed.walk("VEVENT")] == [str(e["UID"]) for e in events]


def test_build_calendar_sorted_by_day(lunch_days: tuple[MenuDay, ...]) -> None:
    cal = build_calendar(SITE, LUNCH, reversed(lunch_days), stamp=STAMP)

    assert [e["DTSTART"].dt for e in cal.walk("VEVENT")] == [d.day for d in lunch_days]


def test_build_calendar_deterministic_except_dtstamp(lunch_days: tuple[MenuDay, ...]) -> None:
    first = build_calendar(SITE, LUNCH, lunch_days, stamp=STAMP)
    second = build_calendar(SITE, LUNCH, lunch_days, stamp=datetime(2027, 1, 1, tzinfo=UTC))

    def without_stamp(cal: icalendar.Calendar) -> list[str]:
        return [line for line in ics_lines(cal) if not line.startswith("DTSTAMP")]

    assert ics_lines(first) != ics_lines(second)
    assert without_stamp(first) == without_stamp(second)


def test_summary_and_description(lunch_days: tuple[MenuDay, ...]) -> None:
    pupusa = lunch_days[2]

    assert summary(SITE, LUNCH, pupusa) == "Lunch: Birria and Cheese Pupusa or Bean and Cheese Pupusa"
    assert summary(SITE, LUNCH, pupusa, full=True) == (
        "Chambers Primary Lunch: Birria and Cheese Pupusa or Bean and Cheese Pupusa"
    )
    assert description(pupusa) == (
        "Lunch Entree:\nBirria and Cheese Pupusa\nOr Bean and Cheese Pupusa\n\n"
        "Vegetables:\n• Red Gold Salsa Cup\n• Bush's Taco Fiesta Black Beans\n• Golden Corn Kernels\n\n"
        "Fruit:\n• Diced Peaches Cup\n• Assorted Seasonal Fresh Fruit\n\n"
        "Milk:\n• 1% White Milk\n• Fat Free Chocolate Milk"
    )


def test_breakfast_headline(breakfast_month: Any) -> None:
    day = parse_month(breakfast_month["data"])[0]

    assert summary(SITE, BREAKFAST, day) == (
        "Breakfast: Apple Cinnamon French Toast or Assorted General Mills Cereal Bowls or Benefit Bars"
    )
    assert summary(SITE, BREAKFAST, day, full=True) == (
        "Chambers Primary Breakfast: Apple Cinnamon French Toast or Assorted General Mills Cereal Bowls or Benefit Bars"
    )


def test_calendar_name_and_filename() -> None:
    unknown = Menu(id=MenuId(777), name="Snack", meal_type=None, published_months=())

    assert (calendar_name(SITE, LUNCH), feed_filename(SITE, LUNCH)) == (
        "Chambers Primary Lunch",
        "1265-12589-lunch.ics",
    )
    assert (calendar_name(SITE, BREAKFAST), feed_filename(SITE, BREAKFAST)) == (
        "Chambers Primary Breakfast",
        "1265-12589-breakfast.ics",
    )
    assert feed_filename(SITE, unknown) == "1265-12589-menu-777.ics"


def test_rename_keeps_identity(lunch_days: tuple[MenuDay, ...]) -> None:
    renamed = replace(SITE, name="../Chambers  Primary!")
    original = ics_lines(build_calendar(SITE, LUNCH, lunch_days, stamp=STAMP))
    changed = ics_lines(build_calendar(renamed, LUNCH, lunch_days, stamp=STAMP))

    assert feed_filename(renamed, LUNCH) == feed_filename(SITE, LUNCH)
    assert [line for line in original if line.startswith("UID")] == [line for line in changed if line.startswith("UID")]
    differing = {a.split(":", 1)[0] for a, b in zip(original, changed, strict=True) if a != b}
    assert differing == {"NAME", "X-WR-CALNAME"}

    original = ics_lines(build_calendar(SITE, LUNCH, lunch_days, stamp=STAMP, full_titles=True))
    changed = ics_lines(build_calendar(renamed, LUNCH, lunch_days, stamp=STAMP, full_titles=True))
    differing = {a.split(":", 1)[0] for a, b in zip(original, changed, strict=True) if a != b}
    assert differing == {"NAME", "X-WR-CALNAME", "SUMMARY"}


def test_build_calendar_rejects_duplicate_days(lunch_days: tuple[MenuDay, ...]) -> None:
    with pytest.raises(ValueError, match="duplicate menu day 2026-09-03"):
        build_calendar(SITE, LUNCH, [lunch_days[0], lunch_days[1], lunch_days[0]], stamp=STAMP)


def test_summary_without_recipes_is_meal_or_calendar_name() -> None:
    empty = MenuDay(day=date(2026, 9, 1), sections=())

    assert summary(SITE, LUNCH, empty) == "Lunch"
    assert summary(SITE, LUNCH, empty, full=True) == "Chambers Primary Lunch"


def recipe(name: str) -> MenuItem:
    return MenuItem(kind=ItemKind.RECIPE, name=name)


def text(name: str) -> MenuItem:
    return MenuItem(kind=ItemKind.TEXT, name=name)


CEREALS = (recipe("• Corn Flakes"), recipe("• Bran Flakes"))
MILK = Section(title="Milk", items=(recipe("1% Milk"),))


def bulleted_breakfast(day: int, *entrees: str) -> MenuDay:
    """Shaped like districts that bullet entrées and separate choices with text items."""
    items = [recipe(f"• {name}") for name in entrees]
    items += [text("Or"), text("Choice Of"), *CEREALS, text("With"), recipe("Toast"), text("And"), recipe("Jelly")]
    return MenuDay(day=date(2026, 11, day), sections=(Section(title="Breakfast Entree", items=tuple(items)), MILK))


def test_staples_need_enough_days() -> None:
    days = [bulleted_breakfast(d, f"Dish {d}") for d in range(2, 7)]

    assert staples(days) == {"• Corn Flakes", "• Bran Flakes", "Toast", "Jelly", "1% Milk"}
    assert staples(days[:4]) == frozenset()


def test_short_titles_keep_only_daily_bulleted_entrees() -> None:
    days = [bulleted_breakfast(d, f"Dish {d}") for d in range(2, 7)]
    days.append(bulleted_breakfast(9, "Egg Casserole🐖", "Waffles"))
    cal = build_calendar(SITE, BREAKFAST, days, stamp=STAMP)

    titles = [str(e["SUMMARY"]) for e in cal.walk("VEVENT")]
    assert titles[0] == "Breakfast: Dish 2"
    assert titles[-1] == "Breakfast: Egg Casserole 🐖 or Waffles"


def test_short_title_when_only_staples_are_bulleted() -> None:
    cereal_only = bulleted_breakfast(10)
    cereals = {item.name for item in CEREALS}

    assert short_headline(cereal_only, cereals) == ("Toast", "Jelly")


def test_short_title_keeps_inner_punctuation() -> None:
    day = MenuDay(
        day=date(2026, 11, 2),
        sections=(
            Section(
                title="Signature Selection",
                items=(
                    recipe("• *STATE* Pork Sandwich🐖"),
                    text("Or"),
                    recipe("• Nachos🐄🐖"),
                    text("With"),
                    recipe("Corn"),
                ),
            ),
        ),
    )

    assert summary(SITE, LUNCH, day) == "Lunch: *STATE* Pork Sandwich 🐖 or Nachos 🐄🐖"


def test_custom_calendar_name_is_cleaned(lunch_days: tuple[MenuDay, ...]) -> None:
    lines = ics_lines(build_calendar(SITE, LUNCH, lunch_days, name="Kid\nX-INJ:1\x07", stamp=STAMP))

    assert "X-WR-CALNAME:Kid X-INJ:1" in lines
    assert not any(line.startswith("X-INJ") for line in lines)
    assert (
        ics_lines(build_calendar(SITE, LUNCH, lunch_days, name=" \x00 ", stamp=STAMP)).count(
            "X-WR-CALNAME:Chambers Primary Lunch"
        )
        == 1
    )


def test_lone_surrogate_from_json_is_removed_and_serializes() -> None:
    setting = json.dumps({"current_display": [{"type": "recipe", "name": "Pizza\ud800X"}]})
    assert "\\ud800" in setting  # the upstream payload carries an escaped lone surrogate
    day = parse_month([{"day": "2026-09-08", "setting": setting}])[0]

    assert day.headline == ("PizzaX",)
    assert b"PizzaX" in build_calendar(SITE, LUNCH, [day], stamp=STAMP).to_ical()


def test_description_joins_choices_into_lines() -> None:
    day = bulleted_breakfast(10, "Egg Casserole🐖")

    assert description(day) == (
        "Breakfast Entree:\n"
        "Egg Casserole 🐖, or choice of:\n"
        "• Corn Flakes\n"
        "• Bran Flakes\n"
        "With Toast and Jelly\n\n"
        "Milk: 1% Milk"
    )


def test_description_skips_leading_connectors_and_keeps_text_dishes() -> None:
    day = MenuDay(
        day=date(2026, 11, 2),
        sections=(
            Section(
                title=None,
                items=(text("With"), text("Or"), recipe("• Cereal"), text("With"), text("Hot Roll"), text("Or")),
            ),
        ),
    )

    assert description(day) == "Cereal\nWith Hot Roll"


def test_description_list_after_list_gets_its_own_connector_line() -> None:
    day = MenuDay(
        day=date(2026, 11, 2),
        sections=(
            Section(
                title="Entree",
                items=(recipe("A"), recipe("B"), text("Or"), recipe("C"), recipe("D"), text("And"), recipe("E")),
            ),
        ),
    )

    assert description(day) == "Entree:\n• A\n• B\nOr:\n• C\n• D\n• E"


def test_description_drops_empty_sections() -> None:
    day = MenuDay(
        day=date(2026, 11, 2),
        sections=(Section(title="Entree", items=(recipe("A"),)), Section(title="Misc.", items=(text("Or"),))),
    )

    assert description(day) == "Entree: A"

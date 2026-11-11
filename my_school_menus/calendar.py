from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import icalendar
from icalendar.enums import TRANSP

from .models import ItemKind, Menu, MenuDay, Section, Site, clean_text

PRODID = "-//andrewdefilippis//my-school-menus//EN"
UID_DOMAIN = "my-school-menus"
DEFAULT_REFRESH_INTERVAL = timedelta(hours=12)
# A dish served on at least this share of a feed's days (cereal, milk) is left out of short titles.
STAPLE_SHARE = 0.5
# Below this many days there is too little data to tell a staple from a dish that happened to repeat.
STAPLE_MIN_DAYS = 5
# Leading markers some districts put before entrées, e.g. "• Cheeseburger".
BULLETS = "•·▪●◦"
_EMOJI = "\U0001f300-\U0001faff\u2600-\u27bf"
# Text items menus use to join dishes; matched case-insensitively. Other text items are shown as dishes.
CONNECTORS = frozenset({"or", "with", "and", "choice of"})
LIST_BULLET = "• "
_TIGHT_EMOJI = re.compile(f"(?<=[^\\s{_EMOJI}])(?=[{_EMOJI}])")


def meal_label(menu: Menu) -> str:
    return menu.meal_type.name.title() if menu.meal_type else "Menu"


def _meal_key(menu: Menu) -> str:
    return menu.meal_type.name.lower() if menu.meal_type else f"menu-{menu.id}"


def event_uid(site: Site, menu: Menu, day: MenuDay) -> str:
    return f"{site.id}-{menu.id}-{day.day.isoformat()}@{UID_DOMAIN}"


def calendar_uid(site: Site, menu: Menu) -> str:
    return f"{site.district}-{site.id}-{_meal_key(menu)}@{UID_DOMAIN}"


def calendar_name(site: Site, menu: Menu) -> str:
    return f"{site.name} {meal_label(menu)}"


def feed_filename(site: Site, menu: Menu) -> str:
    """Built only from IDs and our own enum so the subscription URL survives renames upstream."""
    return f"{site.district}-{site.id}-{_meal_key(menu)}.ics"


def staples(days: Iterable[MenuDay]) -> frozenset[str]:
    """Recipe names served on at least STAPLE_SHARE of the days, once there are STAPLE_MIN_DAYS or more."""
    counts: Counter[str] = Counter()
    total = 0
    for day in days:
        total += 1
        counts.update({item.name for section in day.sections for item in section.items if item.kind is ItemKind.RECIPE})
    if total < STAPLE_MIN_DAYS:
        return frozenset()
    return frozenset(name for name, count in counts.items() if count >= total * STAPLE_SHARE)


def _is_bulleted(name: str) -> bool:
    return name[:1] in BULLETS


def _tidy(name: str) -> str:
    return _TIGHT_EMOJI.sub(" ", name.lstrip(BULLETS).strip())


def short_headline(day: MenuDay, staple_names: Collection[str] = frozenset()) -> tuple[str, ...]:
    """The day's entrées without everyday items; when the menu marks entrées with bullets, only those."""
    dishes = [name for name in day.headline if name not in staple_names]
    if any(_is_bulleted(name) for name in dishes):
        dishes = [name for name in dishes if _is_bulleted(name)]
    return tuple(_tidy(name) for name in dishes)


def summary(
    site: Site, menu: Menu, day: MenuDay, *, full: bool = False, staple_names: Collection[str] = frozenset()
) -> str:
    """Short by default: "Lunch: Cheeseburger or Hot Dog". ``full`` restores "<School> Lunch: <every entrée>"."""
    if full:
        name = calendar_name(site, menu)
        return f"{name}: {' or '.join(day.headline)}" if day.headline else name
    label = meal_label(menu)
    dishes = short_headline(day, staple_names)
    return f"{label}: {' or '.join(dishes)}" if dishes else label


def _choice_groups(section: Section) -> list[tuple[str, list[str]]]:
    """Split a section into (connector, dishes) runs, e.g. ("or choice of", ["Cocoa Puffs", "Trix Cereal"]).

    Connector text items ("Or", "With", "And", "Choice of") start a new run; any other text item is treated as a
    dish, because some menus type dishes as text. Connectors before the first dish have nothing to connect.
    """
    groups: list[tuple[str, list[str]]] = []
    pending: list[str] = []
    for item in section.items:
        if item.kind is ItemKind.TEXT and item.name.casefold() in CONNECTORS:
            pending.append(item.name.casefold())
            continue
        name = _tidy(item.name)
        if not name:
            continue
        if pending or not groups:
            groups.append((" ".join(pending) if groups else "", [name]))
            pending = []
        else:
            groups[-1][1].append(name)
    return groups


def _section_lines(section: Section) -> list[str]:
    """A single dish per line ("With Muffin Square"); two or more options as a bulleted list introduced at the end
    of the line above ("Tot N Egg Casserole, or choice of:")."""
    lines: list[str] = []
    for connector, dishes in _choice_groups(section):
        after_list = not lines or lines[-1].startswith(LIST_BULLET)
        if connector == "and" and lines and after_list:  # "and" after a list adds to that list
            lines += [f"{LIST_BULLET}{dish}" for dish in dishes]
            continue
        if len(dishes) == 1:
            if not connector:
                lines.append(dishes[0])
            elif connector == "and" and not after_list:
                lines[-1] += f" and {dishes[0]}"
            else:
                lines.append(f"{connector.capitalize()} {dishes[0]}")
            continue
        if connector and not after_list:
            lines[-1] += f", {connector}:"
        elif connector:
            lines.append(f"{connector.capitalize()}:")
        lines += [f"{LIST_BULLET}{dish}" for dish in dishes]
    return lines


def description(day: MenuDay) -> str:
    blocks = []
    for section in day.sections:
        lines = _section_lines(section)
        if not lines:  # a heading with nothing under it
            continue
        if not section.title:
            blocks.append("\n".join(lines))
        elif len(lines) == 1 and not lines[0].startswith(LIST_BULLET):
            blocks.append(f"{section.title}: {lines[0]}")
        else:
            blocks.append("\n".join([f"{section.title}:", *lines]))
    return "\n\n".join(block for block in blocks if block)


def build_calendar(
    site: Site,
    menu: Menu,
    days: Iterable[MenuDay],
    *,
    name: str | None = None,
    stamp: datetime | None = None,
    refresh_interval: timedelta = DEFAULT_REFRESH_INTERVAL,
    full_titles: bool = False,
) -> icalendar.Calendar:
    stamp = stamp or datetime.now(UTC)
    ordered = sorted(days, key=lambda d: d.day)
    for previous, current in pairwise(ordered):
        if previous.day == current.day:
            raise ValueError(f"duplicate menu day {current.day.isoformat()}: event UIDs must be unique")
    staple_names = frozenset() if full_titles else staples(ordered)
    events = [
        icalendar.Event.new(
            uid=event_uid(site, menu, day),
            stamp=stamp,
            start=day.day,
            summary=summary(site, menu, day, full=full_titles, staple_names=staple_names),
            description=description(day),
            transparency=TRANSP.TRANSPARENT,
        )
        for day in ordered
    ]
    return icalendar.Calendar.new(
        prodid=PRODID,
        name=clean_text(name or "") or calendar_name(site, menu),
        uid=calendar_uid(site, menu),
        refresh_interval=refresh_interval,
        subcomponents=events,
    )

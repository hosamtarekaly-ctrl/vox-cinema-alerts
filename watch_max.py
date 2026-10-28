#!/usr/bin/env python3
"""VOX City Centre Almaza Avengers: Doomsday showtime notifications.

Policy: Dec 16-23, 2026 => MAX only; Dec 24 onward => MAX + Standard.
Dates are discovered from VOX's published date tabs, not guessed.
No logins, booking, or access-control circumvention.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

MOVIE = "Avengers: Doomsday (Infinity Vision)"
VENUE = "City Centre Almaza"
RELEASE = date(2026, 12, 16)
STANDARD_FROM = date(2026, 12, 24)
# Cap anomalous dates far beyond theatrical run; adjust if required.
LAST_MONITORED_DATE = RELEASE + timedelta(days=180)
INDEX_URL = "https://egy.voxcinemas.com/showtimes/city-centre-almaza"
SHOWTIMES_URL = "https://egy.voxcinemas.com/showtimes"
STATE_FILE = Path(__file__).with_name("last_seen.json")

MONTHS = {
    name.lower(): i for i, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
MONTHS.update({"sept": 9})
DATE_LABEL = re.compile(
    r"^(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s+)?(\d{1,2})\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"(?:\s+(\d{4}))?$", re.I
)
DATE_IN_HREF = re.compile(r"(?:[?&](?:d|date)=)(\d{8})(?:\b|&)", re.I)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COMPACT_DATE = re.compile(r"^\d{8}$")
SHOWTIME = re.compile(r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:am|pm)\b", re.I)
FORMATS = {"MAX", "STANDARD", "GOLD", "IMAX", "4DX", "KIDS", "VIP", "PREMIER", "SCREENX", "3D"}
UNAVAILABLE_FLAGS = ("sold-out", "soldout", "unavailable", "disabled", "past-show", "fully-booked", "not-available")
HEADINGS = {f"h{i}" for i in range(1, 7)}


def tidy(value: str) -> str:
    return " ".join(value.split()).strip()


def url_for_day(day: date) -> str:
    return SHOWTIMES_URL + "?" + urlencode({"c": "city-centre-almaza", "d": day.strftime("%Y%m%d")})


def infer_year(month: int, day: int) -> date:
    # For this Dec-2026 release, next Jan/Feb/etc are in 2027.
    year = RELEASE.year if month >= RELEASE.month else RELEASE.year + 1
    return date(year, month, day)


def parse_date_label(s: str) -> date | None:
    match = DATE_LABEL.fullmatch(tidy(s))
    if not match:
        return None
    day_num, month_name, explicit_year = match.groups()
    month = MONTHS[month_name[:4].lower()] if month_name.lower().startswith("sept") else MONTHS[month_name[:3].lower()]
    try:
        return date(int(explicit_year), month, int(day_num)) if explicit_year else infer_year(month, int(day_num))
    except ValueError:
        return None


def from_date_attribute(value: str) -> date | None:
    value = value.strip()
    try:
        if COMPACT_DATE.fullmatch(value):
            return datetime.strptime(value, "%Y%m%d").date()
        if ISO_DATE.fullmatch(value):
            return date.fromisoformat(value)
    except ValueError:
        pass
    return None


def published_dates(html: str) -> list[date]:
    """Discover days shown as clickable date tabs (including future dates)."""
    soup = BeautifulSoup(html, "html.parser")
    found: set[date] = set()
    for node in soup.find_all(["a", "button", "li", "span", "time"]):
        # The text must be a *short, complete* date label, not movie descriptions.
        label = tidy(node.get_text(" ", strip=True))
        if len(label) <= 28:
            parsed = parse_date_label(label)
            if parsed:
                found.add(parsed)
        for attr in ("data-date", "data-day", "data-selected-date", "datetime"):
            value = node.get(attr)
            if isinstance(value, str):
                parsed = from_date_attribute(value)
                if parsed:
                    found.add(parsed)
        href = node.get("href", "")
        if isinstance(href, str):
            match = DATE_IN_HREF.search(href)
            if match:
                parsed = from_date_attribute(match.group(1))
                if parsed:
                    found.add(parsed)
    return sorted(day for day in found if RELEASE <= day <= LAST_MONITORED_DATE)


def selected_date_if_marked(html: str) -> date | None:
    """Only validate a selected tab if the site exposes explicit selection metadata."""
    soup = BeautifulSoup(html, "html.parser")
    selected = set()
    for node in soup.find_all(["a", "button", "li", "span", "time"]):
        classes = node.get("class", [])
        flags = " ".join(str(c).lower() for c in classes)
        if not (node.get("aria-selected") == "true" or node.get("aria-current") in ("date", "true") or
                any(re.search(r"\b" + word + r"\b", flags) for word in ("active", "selected", "current"))):
            continue
        day = parse_date_label(node.get_text(" ", strip=True))
        if day:
            selected.add(day)
    return next(iter(selected)) if len(selected) == 1 else None


def is_unavailable(node: NavigableString) -> bool:
    """Avoid alerting on visibly disabled/struck-through buttons where detectable."""
    parent = node.parent
    # Look near the time label, not at arbitrary ancestors of the movie section.
    for _ in range(5):
        if not isinstance(parent, Tag) or parent.name in {"body", "html"}:
            break
        if parent.name in {"del", "s", "strike"} or parent.has_attr("disabled"):
            return True
        if parent.get("aria-disabled") == "true":
            return True
        flags = " ".join(parent.get("class", [])).lower()
        if any(flag in flags for flag in UNAVAILABLE_FLAGS):
            return True
        style = str(parent.get("style", "")).lower().replace(" ", "")
        if "line-through" in style:
            return True
        parent = parent.parent
    return False


def extract_showtimes(html: str) -> dict[str, list[str]]:
    """Get only MAX/Standard showtimes for this exact movie and this venue.

    No movie heading means no sessions listed on that date, if the parent page
    otherwise looks like a genuine showtimes listing.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if not text or any(needle in text.lower() for needle in ("verify you are human", "enable javascript to continue")):
        raise ValueError("Invalid/challenge page, not a showtimes listing")

    movie_headings = [
        node for node in soup.find_all(list(HEADINGS))
        if tidy(node.get_text(" ", strip=True)).casefold() == MOVIE.casefold()
    ]
    if not movie_headings:
        if "view times and book" not in text.lower() and "select your cinema" not in text.lower():
            raise ValueError("Movie not found and response does not look like a VOX showtimes page")
        return {"MAX": [], "STANDARD": []}

    found = {"MAX": set(), "STANDARD": set()}
    for movie_heading in movie_headings:
        movie_level = int(movie_heading.name[1])
        inside_venue = False
        venue_level: int | None = None
        current_format = None
        for node in movie_heading.next_elements:
            if isinstance(node, Tag) and node.name in HEADINGS:
                level = int(node.name[1])
                if level <= movie_level:
                    break
                heading_label = tidy(node.get_text(" ", strip=True))
                if heading_label.casefold() == VENUE.casefold():
                    inside_venue, venue_level, current_format = True, level, None
                elif inside_venue and venue_level is not None and level <= venue_level:
                    inside_venue, current_format = False, None
            if not isinstance(node, NavigableString) or not node.parent:
                continue
            if node.parent.name in {"script", "style", "noscript", "template"}:
                continue
            raw = tidy(str(node))
            if raw.casefold() == VENUE.casefold():
                inside_venue, current_format = True, None
                continue
            if not inside_venue or not raw:
                continue
            # If venue headings are not semantic, don't accidentally read a different venue.
            if raw.casefold() in {"city centre alexandria", "mall of egypt"}:
                inside_venue, current_format = False, None
                continue
            upper = raw.upper()
            if upper in FORMATS:
                current_format = upper
                continue
            if current_format in found and not is_unavailable(node):
                for time_str in SHOWTIME.findall(raw):
                    found[current_format].add(re.sub(r"\s+", "", time_str).lower())

    def sort_key(time_str: str):
        return datetime.strptime(time_str, "%I:%M%p").time()

    return {k: sorted(v, key=sort_key) for k, v in found.items()}


def allowed_formats(day: date) -> tuple[str, ...]:
    return ("MAX", "STANDARD") if day >= STANDARD_FROM else ("MAX",)


def fetch(session: requests.Session, url: str) -> str:
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "vox_page.html"
        command = [
            "curl", "-L", "-f", "-sS",
            "--connect-timeout", "10",
            "--max-time", "25",
            "-o", str(page_file),
            "-w", "%{url_effective}",
            url,
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise requests.RequestException(
                f"VOX curl failed: {exc}"
            ) from exc

        if result.returncode != 0:
            raise requests.RequestException(
                f"VOX curl error: {result.stderr}"
            )

        final_url = result.stdout.strip()
        if urlparse(final_url).path.endswith("/showtimes"):
            requested = parse_qs(urlparse(url).query).get("d", [])
            returned = parse_qs(urlparse(final_url).query).get("d", [])
            if requested and returned != requested:
                raise ValueError(
                    f"Showtimes date lost in redirect: {final_url}"
                )

        content = page_file.read_bytes()
        if len(content) < 250:
            raise ValueError("Unexpectedly short VOX response")

        return content.decode("utf-8", errors="replace")



def previous_keys() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if "seen" in data:
        if not isinstance(data["seen"], list):
            raise ValueError("last_seen.json has malformed 'seen' field")
        return set(data["seen"])
    # Read old single-date state without re-alerting on Dec 16 MAX times.
    old = data.get("max_showtimes", [])
    return {f"{RELEASE.isoformat()}|MAX|{t}" for t in old}


def session_key(day: date, fmt: str, time: str) -> str:
    return f"{day.isoformat()}|{fmt}|{time}"


def send_telegram(message: str) -> None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secrets")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram rejected the message")


def alert_lines(items: list[tuple[date, str, str]]) -> list[str]:
    ordered = sorted(items, key=lambda item: (item[0], item[1], item[2]))
    output: list[str] = []
    for day, fmt, time in ordered:
        output.append(f"{day.strftime('%a %d %b %Y')} — {fmt} — {time}")
    return output


def notify(items: list[tuple[date, str, str]]) -> None:
    if not items:
        return
    intro = f"🎬 New AVENGERS: DOOMSDAY showtimes!\n{VENUE}\n\n"
    trailer = "\n\nOpen VOX to check/confirm availability:\n" + INDEX_URL
    body = ""
    for line in alert_lines(items):
        if len(intro) + len(body) + len(line) + len(trailer) > 3900:
            send_telegram(intro + body.strip() + trailer)
            body = ""
        body += line + "\n"
    if body:
        send_telegram(intro + body.strip() + trailer)


def check(dry_run: bool) -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PersonalShowtimeWatcher/2.0)",
        "Accept": "text/html,application/xhtml+xml",
        "Cache-Control": "no-cache",
    })
    index_html = fetch(session, INDEX_URL)
    days = published_dates(index_html)
    if not days:
        raise RuntimeError("No published release dates found on VOX; date selector may have changed")

    print("Currently published dates:", ", ".join(d.isoformat() for d in days))
    seen = previous_keys()
    new_sessions: list[tuple[date, str, str]] = []
    errors: list[str] = []
    for day in days:
        try:
            response = fetch(session, url_for_day(day))
            selected = selected_date_if_marked(response)
            if selected is not None and selected != day:
                raise ValueError(f"Requested {day}, but selected tab shows {selected}")
            found = extract_showtimes(response)
            for fmt in allowed_formats(day):
                for time in found[fmt]:
                    key = session_key(day, fmt, time)
                    if key not in seen:
                        new_sessions.append((day, fmt, time))
            print(f"{day.isoformat()}: " + ", ".join(f"{fmt}={found[fmt]}" for fmt in allowed_formats(day)))
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{day.isoformat()}: {exc}")

    if dry_run:
        print("DRY RUN. Would alert on:", alert_lines(new_sessions))
        if errors:
            print("ERRORS:", *errors, sep="\n", file=sys.stderr)
        return 1 if errors else 0

    # Only mark successfully fetched sessions as seen AFTER Telegram delivery.
    if new_sessions:
        notify(new_sessions)
        print(f"Sent alert(s) for {len(new_sessions)} new sessions")
        seen.update(session_key(*item) for item in new_sessions)
    else:
        print("No new matching showtimes")
    STATE_FILE.write_text(json.dumps({"seen": sorted(seen)}, indent=2) + "\n", encoding="utf-8")

    if errors:
        print("Some dates could not be checked (not treated as zero showtimes):", *errors,
              sep="\n", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-telegram", action="store_true", help="Send one notification test")
    parser.add_argument("--dry-run", action="store_true", help="Inspect live dates without alerts or state changes")
    args = parser.parse_args()
    if args.test_telegram:
        send_telegram("✅ VOX showtime watcher: test Telegram message received.")
        return 0
    return check(args.dry_run)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

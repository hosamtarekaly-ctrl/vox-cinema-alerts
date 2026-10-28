# VOX — Avengers: Doomsday showtime watcher (City Centre Almaza)

Monitor **Avengers: Doomsday (Infinity Vision)** at **VOX City Centre Almaza** in Egypt and send **Telegram notifications** when new matching showtimes appear.

## Which dates and formats?

| Screening date | Notification rule |
| --- | --- |
| **16–23 December 2026** | **MAX only** (ignore Standard) |
| **24 December 2026 onward** | **MAX + Standard** (ignore other formats) |

The script discovers dates as they become selectable on VOX's City Centre Almaza showtimes date strip, including later December and January dates. It does **not** assume December 23 is the final day. For safety it ignores dates more than 180 days after December 16, 2026 (`LAST_MONITORED_DATE` can be changed in `watch_max.py`). If a later date is not yet advertised by VOX, there is nothing to check for that date yet. All newly posted times are compared against the saved history of earlier notifications, by **date + format + time**, to avoid duplicate alerts.

**Important limitation:** This detects newly displayed showtimes that are not marked disabled/struck-through in the HTML. A listed showtime does **not** prove ticket booking or seat availability; you must confirm in VOX. Actual markup and date selection behavior have not been live-tested here; run **dry-run** first and inspect the log. The site may change its markup or block scraping. The workflow is not currently active until you install it.

## Setup with GitHub Actions (no always-on computer required)

1. Create a private or public GitHub repository. Upload **the contents of this ZIP** directly into the repository root, **including `.github/workflows/vox-max.yml`**. Confirm the workflow file is inside `.github/workflows/`, not inside an extra enclosing folder.
2. In Telegram, chat with [@BotFather](https://t.me/BotFather), send `/newbot`, and keep the generated **bot token secret**.
3. Open the new bot in Telegram and send `/start`. To find your chat ID, open `https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates` locally and look for `message.chat.id`. Never share the URL/token.
4. In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**; create `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. In **Actions → VOX Doomsday showtime watcher → Run workflow**, choose **test-telegram**. Confirm receipt of a test Telegram message.
6. Run **dry-run** and inspect the log: it should list currently published dates and properly separated MAX and Standard sessions for each day. Dry-run sends no showtime alert and changes no saved state. Do **not** depend on monitoring if the dates/session output looks wrong.
7. Run **check** to begin populating alerts/history. The scheduled workflow will also check approximately every 15 minutes without manual runs.
8. Ensure **Actions → General → Workflow permissions** permits read/write repository contents so updates to `last_seen.json` can be committed. (Some repositories enforce a more restrictive policy.)

If you get an error in the Actions run, it is **not** proof that no slots exist. Fix the error and run dry-run again. Dates that error are not silently saved as zero availability. The workflow attempts to save successfully notified entries even if one date fails.

## How it works

- VOX cinema/date index: https://egy.voxcinemas.com/showtimes/city-centre-almaza
- Specific day page: `https://egy.voxcinemas.com/showtimes?c=city-centre-almaza&d=20261224` (example)
- Four scheduled checks per hour: 07, 22, 37 and 52 minutes UTC. GitHub Actions can run late or skip runs and does not guarantee 15-minute latency.
- During each check, read VOX's published date tabs; fetch showtimes only for advertised dates on or after **16 December 2026** (up to the 180-day safety cap); extract sessions for the named film and cinema; alert on never-before-seen matching date+format+time combinations.
- The script omits struck-through, disabled and sold-out HTML elements when their state is visibly encoded. This is best-effort: site-specific booking availability may require separate verification.
- Each alert contains the screening date, format, time and a link to VOX.
- Does not purchase tickets, log into accounts, bypass security controls, or guarantee real-time availability.
- Avoid polling faster than planned or bypassing CAPTCHAs. Respect VOX site rules.

To test locally: `pip install -r requirements.txt`, `python -m unittest discover -s tests -v`, then `python watch_max.py --dry-run`. Set Telegram bot token and chat ID as environment variables to use `python watch_max.py --test-telegram` or `python watch_max.py`.

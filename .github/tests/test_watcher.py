import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import watch_max
from watch_max import allowed_formats, published_dates, extract_showtimes, session_key, selected_date_if_marked


class WatcherTests(unittest.TestCase):
    def test_published_date_tabs(self):
        html = '''<nav>
          <a href="?c=city-centre-almaza&d=20261216">Wed 16 Dec</a>
          <button>Wed 23 Dec</button><button>Thu 24 Dec</button>
          <a href="?d=20270102">Sat 2 Jan</a><a href="?d=20261027">Today</a>
        </nav>'''
        self.assertEqual(published_dates(html), [
            date(2026,12,16), date(2026,12,23), date(2026,12,24), date(2027,1,2)])

    def test_filters_movie_cinema_format_and_disabled(self):
        html = '''<h1>View Times and Book</h1><h2>Another Movie</h2>
        <h3>City Centre Almaza</h3><h4>MAX</h4><a>9:00pm</a>
        <h2>Avengers: Doomsday (Infinity Vision)</h2>
        <h3>City Centre Alexandria</h3><div>MAX</div><a>2:00pm</a>
        <h3>City Centre Almaza</h3>
        <div>Standard</div><a>11:15am</a>
        <div>MAX</div><a>3:00pm</a><a disabled>4:30pm</a>
        <span style="text-decoration: line-through">6:00pm</span>
        <div>GOLD</div><a>9:15pm</a>
        <h3>Mall of Egypt</h3><div>MAX</div><a>11:30pm</a>
        <h2>End</h2>'''
        found = extract_showtimes(html)
        self.assertEqual(found["MAX"], ["3:00pm"])
        self.assertEqual(found["STANDARD"], ["11:15am"])

    def test_movie_not_listed_is_empty(self):
        html = '<h1>View Times and Book</h1><h2>Other movie</h2><h3>City Centre Almaza</h3>'
        self.assertEqual(extract_showtimes(html), {"MAX": [], "STANDARD": []})

    def test_rule_boundary(self):
        self.assertEqual(allowed_formats(date(2026,12,16)), ("MAX",))
        self.assertEqual(allowed_formats(date(2026,12,23)), ("MAX",))
        self.assertEqual(allowed_formats(date(2026,12,24)), ("MAX", "STANDARD"))
        self.assertEqual(allowed_formats(date(2027,1,8)), ("MAX", "STANDARD"))
        self.assertNotEqual(session_key(date(2026,12,24), "MAX", "7:00pm"),
                            session_key(date(2026,12,24), "STANDARD", "7:00pm"))

    def test_notifications_for_24_dec_and_history_deduplication(self):
        index = '<a href="?d=20261216">Wed 16 Dec</a><a href="?d=20261224">Thu 24 Dec</a>'
        d16 = '''<h1>View Times and Book</h1><h2>Avengers: Doomsday (Infinity Vision)</h2>
            <h3>City Centre Almaza</h3><div>Standard</div><a>1:00pm</a>
            <div>MAX</div><a>3:00pm</a>'''
        d24 = '''<h1>View Times and Book</h1><h2>Avengers: Doomsday (Infinity Vision)</h2>
            <h3>City Centre Almaza</h3><div>Standard</div><a>1:00pm</a>
            <div>MAX</div><a>4:00pm</a>'''
        with TemporaryDirectory() as tmp:
            with patch.object(watch_max, "STATE_FILE", Path(tmp) / "last_seen.json"), \
                 patch.object(watch_max, "fetch", side_effect=[index, d16, d24, index, d16, d24]), \
                 patch.object(watch_max, "notify") as notify:
                self.assertEqual(watch_max.check(dry_run=False), 0)
                notify.assert_called_once()
                self.assertEqual(set(notify.call_args.args[0]), {
                    (date(2026,12,16), "MAX", "3:00pm"),
                    (date(2026,12,24), "MAX", "4:00pm"),
                    (date(2026,12,24), "STANDARD", "1:00pm"),
                })
                self.assertEqual(watch_max.check(dry_run=False), 0)
                notify.assert_called_once()  # same slots must not re-alert

    def test_selected_date(self):
        html = '<nav><a class="active">Wed 16 Dec</a><a>Thu 17 Dec</a></nav>'
        self.assertEqual(selected_date_if_marked(html), date(2026,12,16))


if __name__ == "__main__":
    unittest.main()

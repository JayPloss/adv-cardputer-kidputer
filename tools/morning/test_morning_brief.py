"""Checks for morning-brief.py that need no network, Piper or ffmpeg.

  python3 -I tools/morning/test_morning_brief.py
"""
import sys
sys.dont_write_bytecode = True
import datetime as dt
import importlib.util
import itertools
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("morning_brief", Path(__file__).with_name("morning-brief.py"))
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

KID = {"user": "albie", "name": "Albert", "lat": 45.50, "lon": -73.57}
DATE = dt.date(2026, 11, 25)


def weather(**over):
    w = {"temp": -8, "feels": -15, "wind": 30, "gusts": 55, "now": "snow", "rain_mm": 0, "snow_cm": 3,
         "high": -2, "afternoon": "overcast", "evening_temp": -6, "evening": "clear", "sunrise": "07:01",
         "sunset": "16:11", "low": -9, "next_temp": -12, "next": "clear", "yesterday_high": 1}
    w.update(over)
    return w


class Numbers(unittest.TestCase):
    def test_french(self):
        cases = {-8: "moins huit", 0: "zéro", 1: "un", 21: "vingt et un", 71: "soixante et onze", 80: "quatre-vingts",
                 81: "quatre-vingt-un", 91: "quatre-vingt-onze", 100: "cent", 200: "deux cents", 201: "deux cent un",
                 -17: "moins dix-sept"}
        for n, words in cases.items():
            self.assertEqual(mb.fr_number(n), words)
        self.assertEqual(mb.fr_time_spoken(7, 1), "sept heures une")
        self.assertEqual(mb.fr_time_spoken(1, 0), "une heure")
        self.assertEqual(mb.fr_time_spoken(21, 0), "vingt et une heures")
        self.assertEqual(mb.fr_time_spoken(12, 0), "midi")

    def test_english(self):
        self.assertEqual(mb.en_number(-8), "minus eight")
        self.assertEqual(mb.en_number(45), "forty-five")
        self.assertEqual(mb.en_ordinal(25), "twenty-fifth")
        self.assertEqual(mb.en_ordinal(1), "first")
        self.assertEqual(mb.en_ordinal(20), "twentieth")
        self.assertEqual(mb.en_ordinal(12), "twelfth")
        self.assertEqual(mb.en_time_spoken(16, 11), "four eleven in the afternoon")
        self.assertEqual(mb.en_time_spoken(8, 5), "eight oh five")

    def test_render(self):
        text = mb.compose(KID, DATE, weather(), [], "fr", [])
        self.assertIn("il fait -8 degrés, ressenti -15", mb.render(text, "fr", False))
        self.assertIn("il fait moins huit degrés, ressenti moins quinze", mb.render(text, "fr", True))
        self.assertNotIn("\x01", mb.render(text, "fr", True))
        self.assertEqual(mb.rnd(-0.4), 0)
        self.assertEqual(str(mb.rnd(-0.4)), "0")


class Descriptive(unittest.TestCase):
    def test_detector_catches_prescriptions(self):
        self.assertTrue(mb.prescriptive_words("Il fait froid, mets ta tuque.", "fr"))
        self.assertTrue(mb.prescriptive_words("N'oublie pas ton parapluie.", "fr"))
        self.assertTrue(mb.prescriptive_words("It's cold, wear your hat.", "en"))
        self.assertTrue(mb.prescriptive_words("Don't forget your boots!", "en"))
        with self.assertRaises(ValueError):
            mb.check_descriptive("Put on your coat.", "en")

    def test_generated_text_never_prescribes(self):
        """Every condition, wind strength, precipitation and tier, in both languages, shown and spoken."""
        events = [{"time": "08:15", "fr": "Natation", "en": "Swimming"}, {"time": "", "fr": "Pyjama", "en": "Pyjama day"}]
        checked = 0
        for now, wind, (rain, snow), temp in itertools.product(
                mb.SEVERITY, (0, 15, 30, 50), ((0, 0), (0.4, 0), (6, 0), (0, 0.5), (0, 12), (3, 2)), (-25, -1, 0, 1, 18, 31)):
            w = weather(now=now, afternoon=now, evening=now, next=now, wind=wind, gusts=wind + 20, rain_mm=rain,
                        snow_cm=snow, temp=temp, feels=temp - 6, high=temp + 4)
            for lang in ("fr", "en"):
                for text in (mb.compose(KID, DATE, w, events, lang, mb.TIERS), mb.compose(KID, DATE, w, [], lang, [])):
                    for spoken in (False, True):
                        rendered = mb.render(text, lang, spoken)
                        self.assertEqual(mb.prescriptive_words(rendered, lang), [], rendered)
                        checked += 1
                self.assertEqual(mb.prescriptive_words(mb.weather_line(w, lang), lang), [])
        self.assertGreater(checked, 4000)

    def test_moon_and_seasons_over_a_year(self):
        for offset in range(366):
            day = DATE + dt.timedelta(days=offset)
            for lang in ("fr", "en"):
                for sentence in (mb.moon_sentence(day, lang), mb.render(mb.season_sentence(day, lang), lang, False)):
                    self.assertEqual(mb.prescriptive_words(sentence, lang), [])
        self.assertEqual(mb.render(mb.season_sentence(DATE, "fr"), "fr", False), "L'hiver commence dans 26 jours.")
        self.assertEqual(mb.season_sentence(dt.date(2026, 12, 21), "fr"), "C'est le premier jour de l'hiver!")


class Agenda(unittest.TestCase):
    def test_weekday_and_date_entries(self):
        agenda = {"albie": [
            {"weekday": "mercredi", "time": "08:15", "fr": "Natation", "en": "Swimming"},
            {"weekday": "thursday", "time": "09:00", "fr": "Piano", "en": "Piano"},
            {"date": "2026-11-25", "time": "", "fr": "Journée pyjama", "en": "Pyjama day"},
            {"date": "2026-11-25", "time": "07:30", "fr": "Autobus tôt", "en": "Early bus"}]}
        events = mb.events_for(agenda, "albie", DATE)
        self.assertEqual([e["en"] for e in events], ["Early bus", "Swimming", "Pyjama day"])
        self.assertEqual(mb.events_for(agenda, "juju", DATE), [])
        with self.assertRaises(ValueError):
            mb.events_for({"albie": [{"date": "2026-11-25", "time": "8:15", "fr": "x", "en": "x"}]}, "albie", DATE)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Kidputer Morning Overview v2: build each kid's brief (text fr/en + Piper MP3s) and upload it.

Contract: docs/morning-overview.md. Python 3 standard library only; run with `python3 -I`.
Weather comes from Open-Meteo, which receives only each town's coordinates. The text describes
the day; it never tells the kid what to wear or bring (check_descriptive refuses that).

  python3 -I tools/morning/morning-brief.py --dry-run --out /tmp/morning   # build, keep files, no upload
  python3 -I tools/morning/morning-brief.py                                 # build tomorrow's, upload
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
DEFAULT_CONFIG = HOME / ".config/kidputer/morning.json"
DEFAULT_AGENDA = HOME / ".config/kidputer/agenda.json"
PIPER = HOME / ".local/bin/piper"
VOICES = HOME / ".local/share/piper-voices"
VOICE = {"fr": "fr_FR-siwis-medium.onnx", "en": "en_US-lessac-medium.onnx"}
TZ = "America/Toronto"
MIN_S, MAX_S = 45.0, 60.0
MAX_BRIEF_BYTES = 4096
SENTENCE_SILENCE = "0.35"

# ---------------------------------------------------------------- numbers, times and dates in words

FR_UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze",
            "douze", "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf"]
FR_TENS = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}


def fr_number(n, feminine=False):
    if n < 0:
        return "moins " + fr_number(-n, feminine)
    if n < 20:
        word = FR_UNITS[n]
    elif n < 70:
        tens, unit = divmod(n, 10)
        word = FR_TENS[tens * 10] + ("" if unit == 0 else " et un" if unit == 1 else "-" + FR_UNITS[unit])
    elif n < 80:
        word = "soixante" + (" et onze" if n == 71 else "-" + FR_UNITS[n - 60])
    elif n < 100:
        word = "quatre-vingts" if n == 80 else "quatre-vingt-" + FR_UNITS[n - 80]
    elif n < 1000:
        hundreds, rest = divmod(n, 100)
        head = "cent" if hundreds == 1 else FR_UNITS[hundreds] + (" cents" if rest == 0 else " cent")
        word = head if rest == 0 else head + " " + fr_number(rest)
    else:
        return str(n)
    if feminine and (word == "un" or word.endswith(" et un")):
        word += "e"
    return word


EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
            "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def en_number(n):
    if n < 0:
        return "minus " + en_number(-n)
    if n < 20:
        return EN_UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return EN_TENS[tens] + ("" if unit == 0 else "-" + EN_UNITS[unit])
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return EN_UNITS[hundreds] + " hundred" + ("" if rest == 0 else " and " + en_number(rest))
    return str(n)


EN_ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
              "nine": "ninth", "twelve": "twelfth"}


def en_ordinal(n):
    words = en_number(n)
    head, _, last = words.rpartition("-")
    if last in EN_ORDINAL:
        last = EN_ORDINAL[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return (head + "-" if head else "") + last


def en_suffix(n):
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# Text is built once with markers, then rendered twice: for the screen (digits) and for Piper (words).
MARK = re.compile(r"\x01(\w):([^\x02]*)\x02")


def N(n):
    return f"\x01n:{n}\x02"


def T(hhmm):
    return f"\x01t:{hhmm}\x02"


def D(day):
    return f"\x01d:{day}\x02"


def clean(text):
    return re.sub(r"[\x00-\x1f]", " ", str(text)).strip()


def fr_time_spoken(h, m):
    if h == 12 and m == 0:
        return "midi"
    if h == 0 and m == 0:
        return "minuit"
    hours = fr_number(h, feminine=True) + (" heure" if h <= 1 else " heures")
    return hours if m == 0 else hours + " " + fr_number(m, feminine=True)


def en_time_spoken(h, m):
    if h == 12 and m == 0:
        return "noon"
    h12 = h % 12 or 12
    base = en_number(h12) + (" o'clock" if m == 0 else " oh " + en_number(m) if m < 10 else " " + en_number(m))
    if h >= 18:
        return base + " in the evening"
    if h >= 12:
        return base + " in the afternoon"
    return base


def en_time_shown(h, m):
    if h >= 12:
        return f"{h % 12 or 12}:{m:02d} pm"
    return f"{h}:{m:02d}"


def render(text, lang, spoken):
    def one(match):
        kind, value = match.group(1), match.group(2)
        if kind == "n":
            n = int(value)
            if not spoken:
                return str(n)
            return fr_number(n) if lang == "fr" else en_number(n)
        if kind == "t":
            h, m = (int(x) for x in value.split(":"))
            if lang == "fr":
                return fr_time_spoken(h, m) if spoken else (f"{h} h" if m == 0 else f"{h} h {m:02d}")
            return en_time_spoken(h, m) if spoken else en_time_shown(h, m)
        if kind == "d":
            n = int(value)
            if lang == "fr":
                return ("premier" if n == 1 else fr_number(n)) if spoken else ("1er" if n == 1 else str(n))
            return en_ordinal(n) if spoken else f"{n}{en_suffix(n)}"
        return value
    return MARK.sub(one, text)


# ---------------------------------------------------------------- describing, never prescribing

PRESCRIPTIVE = {
    "fr": [r"\bmets\b", r"\bmettre\b", r"\bmettez\b", r"\bporte\b", r"\bporter\b", r"\bportez\b", r"\bhabille",
           r"\bapporte", r"\bn'oublie", r"\boublie pas\b", r"\btuque", r"\bmanteau", r"\bbottes?\b", r"\bmitaines?\b",
           r"\bgants?\b", r"\bfoulard", r"\bparapluie", r"\bcrème solaire", r"\bil faut\b", r"\btu dois\b",
           r"\bpense à\b", r"\bprends?\b"],
    "en": [r"\bwear", r"\bput on\b", r"\bbring\b", r"\bdress", r"\bdon't forget\b", r"\bremember to\b", r"\bcoat\b",
           r"\bjacket\b", r"\bhat\b", r"\btoque\b", r"\bboots?\b", r"\bmittens?\b", r"\bgloves?\b", r"\bscarf\b",
           r"\bumbrella\b", r"\bsunscreen\b", r"\byou should\b", r"\byou need\b", r"\bmake sure\b", r"\btake\b"],
}


def prescriptive_words(text, lang):
    """The prescriptive words found in generated text (agenda entries are Jay's own and not checked)."""
    return [p for p in PRESCRIPTIVE[lang] if re.search(p, text, re.IGNORECASE)]


def check_descriptive(text, lang):
    found = prescriptive_words(text, lang)
    if found:
        raise ValueError(f"{lang} text prescribes ({', '.join(found)}): {text}")


# ---------------------------------------------------------------- weather

def condition(code):
    """WMO weather code -> condition key."""
    if code in (0, 1):
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "overcast"
    if code in (45, 48):
        return "fog"
    if code in (56, 57, 66, 67):
        return "freezing"
    if 51 <= code <= 55:
        return "drizzle"
    if 61 <= code <= 65 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "overcast"


SEVERITY = ["clear", "partly", "overcast", "fog", "drizzle", "rain", "snow", "freezing", "storm"]
NOW = {  # present tense, a whole sentence
    "fr": {"clear": "Le ciel est dégagé.", "partly": "Il y a quelques nuages.", "overcast": "Le ciel est gris.",
           "fog": "Il y a du brouillard.", "drizzle": "Il bruine.", "rain": "Il pleut.", "snow": "Il neige.",
           "freezing": "Il tombe de la pluie verglaçante.", "storm": "Il y a de l'orage."},
    "en": {"clear": "The sky is clear.", "partly": "There are a few clouds.", "overcast": "The sky is grey.",
           "fog": "It's foggy.", "drizzle": "It's drizzling.", "rain": "It's raining.", "snow": "It's snowing.",
           "freezing": "Freezing rain is falling.", "storm": "There's a thunderstorm."},
}
WITH = {  # "with ..." after a temperature
    "fr": {"clear": "avec du soleil", "partly": "avec quelques nuages", "overcast": "sous un ciel gris",
           "fog": "avec du brouillard", "drizzle": "avec de la bruine", "rain": "avec de la pluie",
           "snow": "avec de la neige", "freezing": "avec de la pluie verglaçante", "storm": "avec des orages"},
    "en": {"clear": "with sunshine", "partly": "with a few clouds", "overcast": "under grey skies",
           "fog": "with fog", "drizzle": "with drizzle", "rain": "with rain", "snow": "with snow",
           "freezing": "with freezing rain", "storm": "with thunderstorms"},
}
SHORT = {  # the WEATHER line on the AGENDA screen
    "fr": {"clear": "le ciel est dégagé", "partly": "quelques nuages", "overcast": "le ciel est gris",
           "fog": "il y a du brouillard", "drizzle": "il bruine", "rain": "il pleut", "snow": "il neige",
           "freezing": "pluie verglaçante", "storm": "il y a de l'orage"},
    "en": {"clear": "clear", "partly": "partly cloudy", "overcast": "cloudy", "fog": "foggy", "drizzle": "drizzling",
           "rain": "raining", "snow": "snowing", "freezing": "freezing rain", "storm": "stormy"},
}


def wind_class(kmh):
    return 0 if kmh < 10 else 1 if kmh < 25 else 2 if kmh < 40 else 3


def rnd(x):
    return int(round(x)) + 0  # + 0 turns -0 into 0


def fetch_weather(lat, lon, date):
    """Open-Meteo forecast for date and the day after. Only the coordinates leave the machine."""
    query = urllib.parse.urlencode({
        "latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "timezone": TZ, "wind_speed_unit": "kmh",
        "start_date": (date - dt.timedelta(days=1)).isoformat(), "end_date": (date + dt.timedelta(days=1)).isoformat(),
        "hourly": "temperature_2m,apparent_temperature,precipitation,rain,showers,snowfall,weather_code,"
                  "wind_speed_10m,wind_gusts_10m",
        "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min,rain_sum,showers_sum,snowfall_sum",
    })
    request = urllib.request.Request(f"https://api.open-meteo.com/v1/forecast?{query}",
                                     headers={"User-Agent": "kidputer-morning"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def summarize(raw, date):
    """Reduce an Open-Meteo answer to what the brief says. Hours are local (America/Toronto)."""
    h = raw["hourly"]
    index = {t: i for i, t in enumerate(h["time"])}

    def at(day, hour, key):
        return h[key][index[f"{day.isoformat()}T{hour:02d}:00"]]

    def span(day, hours, key):
        return [at(day, hr, key) for hr in hours]

    def worst(day, hours):
        return max((condition(c) for c in span(day, hours, "weather_code")), key=SEVERITY.index)

    tomorrow = date + dt.timedelta(days=1)
    yesterday = date - dt.timedelta(days=1)
    d = raw["daily"]
    i = d["time"].index(date.isoformat())
    morning_hours = range(7, 9)
    return {
        "temp": rnd(at(date, 7, "temperature_2m")),
        "feels": rnd(at(date, 7, "apparent_temperature")),
        "wind": rnd(max(span(date, morning_hours, "wind_speed_10m"))),
        "gusts": rnd(max(span(date, morning_hours, "wind_gusts_10m"))),
        "now": worst(date, morning_hours),
        "rain_mm": (d["rain_sum"][i] or 0) + (d["showers_sum"][i] or 0),
        "snow_cm": d["snowfall_sum"][i] or 0,
        "high": rnd(max(span(date, range(12, 17), "temperature_2m"))),
        "afternoon": worst(date, range(12, 17)),
        "evening_temp": rnd(at(date, 18, "temperature_2m")),
        "evening": worst(date, range(17, 21)),
        "sunrise": d["sunrise"][i][11:16],
        "sunset": d["sunset"][i][11:16],
        "low": rnd(d["temperature_2m_min"][i]),
        "next_temp": rnd(at(tomorrow, 7, "temperature_2m")),
        "next": worst(tomorrow, range(7, 9)),
        "yesterday_high": rnd(max(span(yesterday, range(12, 17), "temperature_2m"))),
    }


# ---------------------------------------------------------------- agenda

WEEKDAYS = {name: i for i, names in enumerate([
    ("monday", "mon", "lundi"), ("tuesday", "tue", "mardi"), ("wednesday", "wed", "mercredi"),
    ("thursday", "thu", "jeudi"), ("friday", "fri", "vendredi"), ("saturday", "sat", "samedi"),
    ("sunday", "sun", "dimanche")]) for name in names}


def load_agenda(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def events_for(agenda, user, date):
    events = []
    for entry in agenda.get(user, []):
        on = entry.get("date") == date.isoformat() or (
            str(entry.get("weekday", "")).lower() in WEEKDAYS and WEEKDAYS[str(entry["weekday"]).lower()] == date.weekday())
        if not on:
            continue
        time = str(entry.get("time") or "")
        if time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
            raise ValueError(f"agenda: bad time {time!r} for {user}")
        fr, en = clean(entry.get("fr") or entry.get("en") or ""), clean(entry.get("en") or entry.get("fr") or "")
        if fr:
            events.append({"time": time, "fr": fr, "en": en})
    return sorted(events, key=lambda e: (e["time"] == "", e["time"]))


# ---------------------------------------------------------------- the text

FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]
EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
# Detail tiers, added in this order until the audio reaches MIN_S.
TIERS = ["afternoon", "evening", "sun", "wind", "tomorrow", "yesterday", "daylight", "moon", "season"]
SYNODIC = 29.530588853
NEW_MOON = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)


def moon_sentence(date, lang):
    """The moon's look this evening (about 20:00 Eastern), from its mean age."""
    evening = dt.datetime(date.year, date.month, date.day, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    phase = ((evening - NEW_MOON).total_seconds() / 86400 / SYNODIC) % 1
    if phase < 0.03 or phase > 0.97:
        return "Ce soir, c'est la nouvelle lune: elle est toute noire." if lang == "fr" else "Tonight is a new moon, so it's all dark."
    if 0.47 <= phase <= 0.53:
        return "Ce soir, la lune est pleine." if lang == "fr" else "Tonight the moon is full."
    growing = phase < 0.5
    if lang == "fr":
        look = ("un mince croissant" if phase < 0.22 or phase > 0.78 else "une demi-lune" if phase < 0.28 or phase > 0.72
                else "une lune presque pleine")
        return f"Ce soir, on peut voir {look}, qui {'grossit' if growing else 'rapetisse'} chaque nuit."
    look = ("a thin crescent" if phase < 0.22 or phase > 0.78 else "a half moon" if phase < 0.28 or phase > 0.72
            else "an almost full moon")
    return f"Tonight there's {look}, {'growing' if growing else 'shrinking'} a little every night."


SEASONS = [((3, 20), "le printemps", "du printemps", "Spring"), ((6, 21), "l'été", "de l'été", "Summer"),
           ((9, 22), "l'automne", "de l'automne", "Fall"), ((12, 21), "l'hiver", "de l'hiver", "Winter")]


def season_sentence(date, lang):
    starts = sorted((dt.date(date.year + y, m, d), fr, fr_of, en) for y in (0, 1) for (m, d), fr, fr_of, en in SEASONS)
    start, fr, fr_of, en = next(x for x in starts if x[0] >= date)
    days = (start - date).days
    if days == 0:
        return f"C'est le premier jour {fr_of}!" if lang == "fr" else f"Today is the first day of {en.lower()}!"
    if lang == "fr":
        return f"{fr[0].upper() + fr[1:]} commence dans {N(days)} jour{'s' if days > 1 else ''}."
    return f"{en} starts in {N(days)} day{'s' if days > 1 else ''}."


def degrees(n, lang):
    if lang == "fr":
        return f"{N(n)} degré" + ("s" if abs(n) >= 2 else "")
    return f"{N(n)} degree" + ("" if abs(n) == 1 else "s")


def weather_line(w, lang):
    wind = wind_class(w["wind"])
    if lang == "fr":
        parts = [f"Il fait {w['temp']}"]
        if wind >= 2:
            parts.append("il vente fort" if wind == 3 else "il vente")
        parts.append(SHORT["fr"][w["now"]])
        return ", ".join(parts) + "."
    words = []
    if wind >= 2:
        words.append("very windy" if wind == 3 else "windy")
    if w["now"] == "freezing":
        return f"It's {w['temp']}" + (f", {words[0]}" if words else "") + ", with freezing rain."
    words.append(SHORT["en"][w["now"]])
    return f"It's {w['temp']}, " + (" and ".join(words) if len(words) == 2 else words[0]) + "."


def precipitation(w, lang):
    rain, snow = w["rain_mm"], w["snow_cm"]
    fr = lang == "fr"
    if rain < 0.2 and snow < 0.2:
        return "Pas de pluie ni de neige prévues aujourd'hui." if fr else "No rain or snow is expected today."
    parts = []
    if snow >= 0.2:
        cm = rnd(snow)
        if cm < 1:
            parts.append("un peu de neige" if fr else "a little snow")
        else:
            parts.append(f"environ {N(cm)} centimètre{'s' if cm >= 2 else ''} de neige" if fr
                         else f"about {N(cm)} centimetre{'s' if cm >= 2 else ''} of snow")
    if rain >= 0.2:
        mm = rnd(rain)
        if mm < 1:
            parts.append("un peu de pluie" if fr else "a little rain")
        else:
            parts.append(f"environ {N(mm)} millimètre{'s' if mm >= 2 else ''} de pluie" if fr
                         else f"about {N(mm)} millimetre{'s' if mm >= 2 else ''} of rain")
    if fr:
        return "Aujourd'hui, il va tomber " + " et ".join(parts) + "."
    return "Today, " + " and ".join(parts) + " should fall."


def event_text(events, lang, limit=None):
    shown = events if limit is None else events[:limit]
    if not shown:
        return "Rien de spécial à l'agenda aujourd'hui." if lang == "fr" else "Nothing special on the agenda today."
    items = []
    for e in shown:
        name = e[lang]
        if e["time"]:
            items.append((f"à {T(e['time'])}, {name}") if lang == "fr" else f"at {T(e['time'])}, {name}")
        else:
            items.append(name)
    more = len(events) - len(shown)
    tail = ""
    if more:
        tail = (f" Et {N(more)} autre{'s' if more > 1 else ''} chose{'s' if more > 1 else ''} à l'écran."
                if lang == "fr" else f" And {N(more)} more on the screen.")
    lead = "À l'agenda aujourd'hui: " if lang == "fr" else "On the agenda today: "
    return lead + "; ".join(items) + "." + tail


def compose(kid, date, w, events, lang, tiers, event_limit=None):
    """The marked-up speech text for one language with the given detail tiers."""
    name = clean(kid["name"])
    fr = lang == "fr"
    s = []
    if fr:
        s.append(f"Bonjour {name}! Nous sommes le {FR_DAYS[date.weekday()]} {D(date.day)} {FR_MONTHS[date.month - 1]}.")
        line = f"Ce matin, il fait {degrees(w['temp'], 'fr')}"
        if abs(w["feels"] - w["temp"]) >= 3:
            line += f", ressenti {N(w['feels'])}"
        s.append(line + ".")
        s.append(NOW["fr"][w["now"]])
        wind = wind_class(w["wind"])
        if wind >= 2:
            s.append("Il vente fort." if wind == 3 else "Il vente.")
    else:
        s.append(f"Good morning {name}! Today is {EN_DAYS[date.weekday()]}, {EN_MONTHS[date.month - 1]} {D(date.day)}.")
        line = f"This morning it's {degrees(w['temp'], 'en')}"
        if abs(w["feels"] - w["temp"]) >= 3:
            line += f", and it feels like {N(w['feels'])}"
        s.append(line + ".")
        s.append(NOW["en"][w["now"]])
        wind = wind_class(w["wind"])
        if wind >= 2:
            s.append("It's very windy." if wind == 3 else "It's windy.")
    if "wind" in tiers:
        if w["wind"] < 5:
            s.append("Il n'y a presque pas de vent." if fr else "There's almost no wind.")
        elif fr:
            s.append(f"Le vent souffle à {N(w['wind'])} kilomètres à l'heure"
                     + (f", avec des rafales jusqu'à {N(w['gusts'])}." if w["gusts"] >= w["wind"] + 10 else "."))
        else:
            s.append(f"The wind is blowing at {N(w['wind'])} kilometres an hour"
                     + (f", with gusts up to {N(w['gusts'])}." if w["gusts"] >= w["wind"] + 10 else "."))
    s.append(precipitation(w, lang))
    if "afternoon" in tiers:
        s.append(f"Cet après-midi, il fera jusqu'à {degrees(w['high'], 'fr')}, {WITH['fr'][w['afternoon']]}." if fr
                 else f"This afternoon, it will go up to {degrees(w['high'], 'en')}, {WITH['en'][w['afternoon']]}.")
    if "yesterday" in tiers:
        diff = w["high"] - w["yesterday_high"]
        if abs(diff) < 2:
            s.append("Cet après-midi ressemblera à celui d'hier." if fr else "This afternoon will feel a lot like yesterday's.")
        elif fr:
            s.append(f"C'est {N(abs(diff))} degrés de {'plus' if diff > 0 else 'moins'} qu'hier après-midi.")
        else:
            s.append(f"That's {N(abs(diff))} degrees {'warmer' if diff > 0 else 'colder'} than yesterday afternoon.")
    if "evening" in tiers:
        s.append(f"Ce soir vers {T('18:00')}, il fera {degrees(w['evening_temp'], 'fr')}, {WITH['fr'][w['evening']]}." if fr
                 else f"This evening around six, it will be {degrees(w['evening_temp'], 'en')}, {WITH['en'][w['evening']]}.")
    if "sun" in tiers:
        s.append(f"Le soleil se lève à {T(w['sunrise'])} et se couche à {T(w['sunset'])}." if fr
                 else f"The sun rises at {T(w['sunrise'])} and sets at {T(w['sunset'])}.")
    if "daylight" in tiers:
        rise = dt.datetime.strptime(w["sunrise"], "%H:%M")
        sets = dt.datetime.strptime(w["sunset"], "%H:%M")
        hours, minutes = divmod(int((sets - rise).total_seconds() // 60), 60)
        s.append(f"Il fera jour pendant {N(hours)} heures et {N(minutes)} minutes." if fr
                 else f"There will be {N(hours)} hours and {N(minutes)} minutes of daylight.")
    if "moon" in tiers:
        s.append(moon_sentence(date, lang))
    s.append(event_text(events, lang, event_limit))
    if "season" in tiers:
        s.append(season_sentence(date, lang))
    if "tomorrow" in tiers:
        s.append(f"Et demain matin, il fera {degrees(w['next_temp'], 'fr')}, {WITH['fr'][w['next']]}." if fr
                 else f"And tomorrow morning, it will be {degrees(w['next_temp'], 'en')}, {WITH['en'][w['next']]}.")
    s.append(f"Bonne journée, {name}!" if fr else f"Have a great day, {name}!")
    return " ".join(s)


# ---------------------------------------------------------------- audio

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout
    return float(out.decode().strip())


def synthesize(spoken, lang, mp3_path, work):
    wav = Path(work) / f"{lang}.wav"
    run([str(PIPER), "-m", str(VOICES / VOICE[lang]), "-f", str(wav), "--sentence-silence", SENTENCE_SILENCE],
        input=spoken.encode("utf-8"))
    # CBR 40 kb/s, 22.05 kHz mono, no ID3 tag and no Xing frame: what the device player expects.
    run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ar", "22050", "-ac", "1", "-c:a", "libmp3lame",
         "-b:a", "40k", "-write_xing", "0", "-id3v2_version", "0", "-map_metadata", "-1", "-f", "mp3", str(mp3_path)])
    return duration(mp3_path)


def build_language(kid, date, w, events, lang, mp3_path, work):
    """Add detail tiers until the audio reaches MIN_S; drop them (then shorten the event list) past MAX_S.

    Piper takes seconds per render, so the tier count is first estimated from the base render's
    speaking rate, then corrected one tier at a time."""
    def attempt(tiers, event_limit=None):
        text = compose(kid, date, w, events, lang, tiers, event_limit)
        return text, synthesize(render(text, lang, True), lang, mp3_path, work)

    tiers, event_limit = [], None
    text, seconds = attempt(tiers)
    if seconds < MIN_S:
        rate = seconds / len(render(text, lang, True))
        count = len(TIERS)
        for n in range(1, len(TIERS) + 1):
            if rate * len(render(compose(kid, date, w, events, lang, TIERS[:n]), lang, True)) >= MIN_S + 2:
                count = n
                break
        tiers = TIERS[:count]
        text, seconds = attempt(tiers)
        while seconds < MIN_S and len(tiers) < len(TIERS):
            tiers = TIERS[:len(tiers) + 1]
            text, seconds = attempt(tiers)
    while seconds > MAX_S and (tiers or (event_limit or len(events)) > 1):
        if tiers:
            tiers = tiers[:-1]
        else:
            event_limit = (event_limit or len(events)) - 1
        text, seconds = attempt(tiers, event_limit)
    shown = render(text, lang, False)
    # Agenda entries are the parent's own words; everything the generator writes is checked.
    check_descriptive(render(compose(kid, date, w, [], lang, tiers), lang, False), lang)
    return {"speech": shown, "spoken": render(text, lang, True), "seconds": seconds, "tiers": tiers}


def build_brief(kid, date, w, events, work, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    brief = {"date": date.isoformat()}
    report = {}
    for lang in ("fr", "en"):
        mp3 = out_dir / f"{date.isoformat()}.{lang}.mp3"
        result = build_language(kid, date, w, events, lang, mp3, work)
        weather = weather_line(w, lang)
        check_descriptive(weather, lang)
        brief[lang] = {"weather": weather, "speech": result["speech"], "audio_s": round(result["seconds"])}
        report[lang] = result
    brief["events"] = events
    size = len(json.dumps({**brief, "version": "00000000"}, ensure_ascii=False, separators=(",", ":")).encode())
    if size > MAX_BRIEF_BYTES:
        raise ValueError(f"{kid['user']}: brief is {size} bytes, over {MAX_BRIEF_BYTES}")
    (out_dir / f"{date.isoformat()}.json").write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
    return brief, report


# ---------------------------------------------------------------- upload

def upload(base, token, user, date, brief, out_dir):
    def put(path, data, content_type):
        request = urllib.request.Request(f"{base}{path}", data=data, method="PUT", headers={
            "Authorization": f"Bearer {token}", "Content-Type": content_type, "User-Agent": "kidputer-morning"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    stem = f"/api/hub/morning/{urllib.parse.quote(user)}/{date.isoformat()}"
    for lang in ("fr", "en"):
        put(f"{stem}/{lang}.mp3", (out_dir / f"{date.isoformat()}.{lang}.mp3").read_bytes(), "audio/mpeg")
    # The text goes last, so the brief is complete when its final version is stamped.
    return put(stem, json.dumps(brief, ensure_ascii=False).encode("utf-8"), "application/json")


# ---------------------------------------------------------------- main

def eastern_today():
    # zoneinfo is stdlib; fall back to the system clock if tzdata is missing.
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(TZ)).date()
    except Exception:
        return dt.date.today()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--agenda", default=str(DEFAULT_AGENDA))
    parser.add_argument("--date", help="YYYY-MM-DD (default: tomorrow, Eastern)")
    parser.add_argument("--only", help="comma-separated device users")
    parser.add_argument("--dry-run", action="store_true", help="build into --out, upload nothing")
    parser.add_argument("--out", help="output folder (default: a temporary one, removed after upload)")
    args = parser.parse_args(argv)

    date = dt.date.fromisoformat(args.date) if args.date else eastern_today() + dt.timedelta(days=1)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    kids = config["kids"]
    if args.only:
        wanted = set(args.only.split(","))
        kids = [k for k in kids if k["user"] in wanted]
    agenda = load_agenda(args.agenda)
    base = os.environ.get("SUPACHAT_URL", "https://supachat.net").rstrip("/")
    token = os.environ.get("SUPACHAT_HUB_TOKEN", "")
    if not args.dry_run and not token:
        sys.exit("SUPACHAT_HUB_TOKEN is not set (use --dry-run to build without uploading)")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found")

    out_root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="kidputer-morning-"))
    weather_cache = {}
    failures = 0
    for kid in kids:
        try:
            where = (round(float(kid["lat"]), 2), round(float(kid["lon"]), 2))
            if where not in weather_cache:
                weather_cache[where] = summarize(fetch_weather(*where, date), date)
            w = weather_cache[where]
            events = events_for(agenda, kid["user"], date)
            out_dir = out_root / kid["user"]
            with tempfile.TemporaryDirectory(prefix="kidputer-morning-wav-") as work:
                brief, report = build_brief(kid, date, w, events, work, out_dir)
            for lang in ("fr", "en"):
                (out_dir / f"{date.isoformat()}.{lang}.spoken.txt").write_text(report[lang]["spoken"] + "\n", encoding="utf-8")
            line = f"{kid['user']}: fr {report['fr']['seconds']:.1f} s, en {report['en']['seconds']:.1f} s"
            if not args.dry_run:
                result = upload(base, token, kid["user"], date, brief, out_dir)
                line += f", uploaded version {result['version']}" + ("" if result.get("complete") else " (incomplete!)")
            print(line, flush=True)
        except (urllib.error.URLError, subprocess.CalledProcessError, ValueError, KeyError) as error:
            failures += 1
            detail = error.stderr.decode(errors="replace").strip() if isinstance(error, subprocess.CalledProcessError) else error
            print(f"{kid.get('user', '?')}: FAILED: {detail}", file=sys.stderr, flush=True)
    if not args.out and not args.dry_run:
        shutil.rmtree(out_root, ignore_errors=True)
    elif args.dry_run:
        print(f"output: {out_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

# Morning briefs (Morning Overview v2)

`morning-brief.py` builds each kid's morning brief for tomorrow and uploads it to SupaChat, where the
Cardputer fetches it (contract: `docs/morning-overview.md`). Per kid and date:

- weather from Open-Meteo for the kid's town (only the coordinates are sent): morning temperature,
  feels-like, wind, rain/snow amounts, afternoon high, evening, sunrise/sunset, tomorrow morning;
- events from `~/.config/kidputer/agenda.json`, if it exists;
- the text in French and English: greeting with the first name, the date, the weather, the events (or
  "nothing special on the agenda"), a closing. It describes the day and never says what to wear or
  bring; the script refuses its own text if it does (`check_descriptive`);
- one MP3 per language, spoken by Piper (`fr_FR-siwis-medium`, `en_US-lessac-medium`), encoded CBR
  40 kb/s, 22.05 kHz mono, without ID3 or Xing header. Detail sentences are added until the audio
  lasts at least 45 s and removed past 60 s.

Python 3 standard library only, plus `piper` (`~/.local/bin/piper`), voices in
`~/.local/share/piper-voices`, `ffmpeg` and `ffprobe`.

## Config

`~/.config/kidputer/morning.json` (copy `morning.example.json`): per kid the SupaChat device user, the
first name for the greeting and the town's coordinates.

`~/.config/kidputer/agenda.json` (optional):

```json
{"albie": [{"weekday": "wednesday", "time": "08:15", "fr": "Natation", "en": "Swimming"},
           {"date": "2026-11-25", "time": "", "fr": "Journée pyjama", "en": "Pyjama day"}]}
```

`weekday` is an English or French day name; `time` is `HH:MM` or empty.

## Run

```sh
# Build tomorrow's briefs into a folder, upload nothing:
python3 -I tools/morning/morning-brief.py --dry-run --out /tmp/morning
# Another date, some kids only:
python3 -I tools/morning/morning-brief.py --dry-run --out /tmp/morning --date 2026-11-26 --only albie,juju
# Build and upload (Papa's hub key; the server accepts admin keys only):
SUPACHAT_HUB_TOKEN=... python3 -I tools/morning/morning-brief.py
```

`SUPACHAT_URL` defaults to `https://supachat.net`. The upload sends both MP3s, then the JSON, to
`PUT /api/hub/morning/<user>/<date>[/<lang>.mp3]`.

Checks that need no network, Piper or ffmpeg: `python3 -I tools/morning/test_morning_brief.py`.

## Nightly timer (not installed)

`kidputer-morning.service` and `kidputer-morning.timer` run it at 20:30 Eastern for tomorrow's date,
catching up after sleep. To install on paqman: put `SUPACHAT_HUB_TOKEN=...` in
`~/.config/kidputer/morning.env` (mode 600), copy both units to `~/.config/systemd/user/`, then
`systemctl --user daemon-reload && systemctl --user enable --now kidputer-morning.timer`.

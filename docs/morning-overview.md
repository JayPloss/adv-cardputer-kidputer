# Always-on clock and Morning Overview v2 (v0.66)

Jay's brief, 2026-11-24 (Jayoverse project 284, revised). Albie's device (formerly Papa's) had no Wi-Fi and its
clock app showed nothing but "clock not set". Both parts ship in one firmware version, v0.66.

## Non-negotiable constraints

- **Off Wi-Fi is a feature.** Clock, alarms, timers, screen time and blackout windows are enforced locally from
  the device's own clock. Only chat and data transfer need the network; chat waits in the persistent outbox and is
  never dropped for being offline.
- **Restrictions belong to parents.** No on-device parent controls beyond what exists.
- Not aggressive: an unknown clock never blocks music, books or anything else harmless (README, Design principles).

## A. The clock is never blank

### Tiers

| Tier | When | Clock screen | Alarms | Quiet-time rules (school night, bedtime book timer) |
|---|---|---|---|---|
| Synced | A time server answered in the last 6 h | The time, nothing else | Ring | Exact |
| Estimated | The chip kept time since the last sync (light sleep, a restart), last sync over 6 h ago | The time, with `ESTIMÉ` / `ESTIMATED` under it | Ring; the alarm list says `~` before times | Over 72 h since the sync: off. Otherwise each window starts and ends 10 min late |
| Unknown | The chip lost the time (power-on, flat battery, a clock behind the high-water mark) | The last time known plus the time since boot, in grey, and one line: `HEURE INCONNUE: WI-FI` / `CLOCK UNKNOWN: NEEDS WI-FI` | Kept and editable, **not rung**: a guessed clock is behind by however long the device was off, so it would ring at a wrong hour | Off |

Timers, screen time and the stopwatch run on the chip's millisecond counter in every tier.

Why the unknown tier doesn't ring: after a flat battery the device has no idea how long it was off. Ringing a 07:00
alarm by that guess would ring at whatever real hour the guess reaches 07:00. Estimated (the chip kept counting) rings.

### Persistence (NVS, namespace `clock`)

- `synced`: UTC seconds of the last time-server answer.
- `boot`: UTC seconds of this boot, as best known.
- `hwm`: high-water mark, the latest trusted time; written every 10 minutes, before sleep and on every sync.

At boot, before anything sets the clock: the chip's clock is kept if the reset was not a power-on, and the time it
reads is not more than 2 minutes behind `hwm`, and the device has synced at least once. Otherwise the clock starts
from `hwm` (or the build time if that is later) and the tier is unknown. A clock 2 minutes or less behind `hwm` is
moved forward to it (no rollback can reopen a closed window).

### Sync

SNTP as before. When Wi-Fi joins and the tier is not synced, SNTP is restarted so the answer comes at once.

Every answer measures the drift exactly (v0.67): the firmware replaces ESP-IDF's weak `sntp_sync_time`, reads the
chip's clock just before setting it, and logs the server's time minus the chip's (negative: the chip ran fast),
the hours since the sync before, and how many of them were in light sleep, which runs on the chip's internal
oscillator. It goes to the serial log (`CLOCK sync drift -1803.0 s after 9.8 h (8.1 h asleep), was estimated,
alarm-check`), to `/supachat-clock.csv` on the card (`utc_s,firmware,tier_before,hours_since_sync,drift_s,
hours_asleep,why`; rows before v0.67 have the first five), and to STATUS page 2 as `DRIFT`.

### Checks before mornings (v0.67)

Albie's 07:00 alarm rang at 06:30 on 2026-11-25; the cause is unproven, and the sleep clock is the likely one.

- **Nightly, 05:00** by the device's own clock (Jay): asleep or awake, a device with a saved network asks the time
  servers once, past the night-time Wi-Fi rule, for at most 2 minutes.
- **Before each alarm:** a sleeping device with a saved network wakes 5 minutes before the alarm by its own
  clock, asks, and rings by the corrected clock. If the correction moved the alarm further away it sleeps and
  checks again; once near with no sleep since the answer, it stays awake (main crystal) through the alarm's
  minute. A corrected clock already past the alarm rings it at once, up to 30 minutes late. No answer in 2
  minutes: it rings by the chip's clock, as before.
- Not in charging mode, local mode or with no saved network. Charging mode keeps the chip awake on its main
  crystal, so its clock doesn't take the sleep drift.
- Runtime emulator: `alarm-drift` and `night-check`, with the chip's clock 50 % fast in light sleep
  (`clock-drift`).

## B. Morning Overview v2

### What the kid sees and hears

1. **Trigger:** the first snooze or stop of a morning alarm (rung between 04:00 and 11:00), once a day. Snooze is
   the better moment: the overview plays during the 5 minutes. A later stop that day opens the screen without
   playing it again (Enter replays).
2. **Content describes, never prescribes:** "Il fait -8, il vente, il neige." No "mets ta tuque". The kids decide.
3. **Length:** 45-60 s spoken.
4. **Language:** the device's LANGUAGE setting. Both languages are downloaded, so switching works offline.
5. **Screens:** MORNING (BONJOUR) shows the same text that is spoken, scrollable. AGENDA (AGENDA on HOME's last
   page) shows the day's events and the weather note, scrollable, to re-read later.
6. **Voice:** Piper, rendered on paqman (sample: `~/Music/kidputer-morning-samples/morning-sample-fr-en.mp3`).
7. **Offline:** text and audio are cached on the card. With no brief for today, the screens show the newest cached
   one under its own date, or, with none at all, the date (by tier), the alarms and one line that the morning
   report needs Wi-Fi once. Never a dead screen.

### The date by clock tier

Synced: `MARDI 25 NOV`. Estimated: the same, with `ESTIMÉ`. Unknown: `DATE INCONNUE` / `DATE UNKNOWN`, and a
cached brief shows its own date.

### Data flow

```
paqman (tools/morning/morning-brief.py, nightly)            supachat.net (server/)                 Cardputer
  weather (Open-Meteo) + events  -> text fr/en          PUT /api/hub/morning/:user/:date         GET /api/device/morning
  Piper -> MP3 22.05 kHz mono 40 kb/s                   PUT .../:date/:lang.mp3                  GET /api/device/morning/:date
                                                         (admin key)                              GET /api/device/morning/:date/:lang.mp3
                                                                                                  -> /morning/ on the card
```

Brief JSON (what the device stores as `/morning/<date>.json`; 4 KB at most):

```json
{"date": "2026-11-25", "version": "a1b2c3d4",
 "fr": {"weather": "Il fait -8, il vente, il neige.", "speech": "Bonjour Albert! ...", "audio_s": 52},
 "en": {"weather": "It's -8, windy and snowing.",     "speech": "Good morning Albert! ...", "audio_s": 50},
 "events": [{"time": "08:15", "fr": "Natation", "en": "Swimming"}]}
```

`GET /api/device/morning` answers `{"briefs": [{"date": "...", "version": "...", "fr_bytes": n, "en_bytes": n}]}`
for today and tomorrow (Eastern). The device fetches what it lacks while on Wi-Fi, one secure connection at a time
(os.h), and keeps three days.

### Where each part runs (places.md)

- The generator runs on paqman, which travels with Jay; it needs the internet only. When paqman is off for a night,
  devices keep yesterday's brief (shown under its own date). A steadier home is ori-san; that move is Jay's call.
- The briefs live on supachat.net, which every device reaches from Grimmauld Place and Chez Josée.
- The device plays and shows them anywhere, offline.

### Open for Jay

- Event sources: none decided (project 258). The generator reads `~/.config/kidputer/agenda.json` until then.

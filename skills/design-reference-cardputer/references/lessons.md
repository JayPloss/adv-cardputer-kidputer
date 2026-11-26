# What this device has already taught us

Each item says where it came from. Add to this list; don't delete from it unless the code that made it true is gone.

## Keys

- **The arrows are not arrow keys.** They share `;` `.` `,` `/`. On a text entry screen they type punctuation and need Fn to navigate; on a navigation screen they navigate without Fn, and Fn stops them. This took two commits and a change of mind to settle ("Fix contextual arrows and French glyph rendering", "fix physical menu navigation"); the code and `emulator/test-flow.mjs` hold the current rule. Every new screen must be listed correctly in `textEntryScreen` in `main.cpp`.
- **The key that dismisses the splash must not reach the next screen.** Boot and charging mode have an input quarantine (`docs/firmware-fixes-backlog.md`, item 1). A new full-screen transition that consumes a held key needs the same thought.
- **A hint on a text entry screen can't name an arrow as the way out.** The Wi-Fi password screen said LEFT CANCEL, but Left without Fn types a comma there (Jay, 2026-11-10). Text screens name the rear button, MENU; a new text screen's hints are checked against its key handler, not against the navigation screens' wording.
- **The French layout is a table, read by key position.** v0.44 to v0.57 remapped Shift+/ to é, so ? could not be typed in French at all (Jay, 2026-11-10). Chat now types CSA from `french_keyboard.h`; its host test fails if any required character or printable ASCII becomes unreachable, and the TOUCHES page is generated from it. Add keys there, never as one-off cases in `main.cpp`.
- **Back has two routes.** The rear button and the top-left key both go back one level (Fn + top-left while typing). A screen reachable from two places must go back to where it came from.

## Sound

- **Feedback tones follow the volume setting, and were painfully loud against speech.** Jay, 2026-11-01: "LOUD bloops and quiet talking are bad for ears." In the listening apps tones now sit at `kListeningToneVolume`. Any new sound goes through the same volume path as `playNextTone()`, and stays silent during recording, playback and charging mode, as that does.

- **Three volumes since v0.67, on speaker channels, not the master.** ALARM, SYSTEM and MEDIA each set their own channel's volume with the master at full (M5Unified squares both and multiplies them). Media plays on channel 1, tones and voice messages on 0. A sound with no channel named takes any free channel at its full 255, so every new `tone()` or `playRaw()` names its channel and sets that channel's volume first: the message notification had none, and would have played at full volume (found writing v0.67; the runtime emulator's `expect-volume` checks it now).

## Text

- **`Font2` has no accents.** French came out wrong until chat moved to `Font0` (commit "Fix contextual arrows and French glyph rendering"). Check any new font with the specimen: an ASCII font can report that it has a letter and still draw the wrong one.
- **`Font0` holds CP437, not UTF-8.** Text that can hold accents must go through `printFont0Text`. A plain `print` draws garbage for anything people typed or names from the server.
- **Font0 has no capital À Â È Ê Ë Î Ï Ô Ù Û.** CP437 holds only É and Ç among French capitals; `font0Cp437Text` draws the others as the plain capital (v0.58).
- **Cut text by character, not by byte.** `utf8Head` and `utf8Tail` exist because `substring` can split an accent in half.
- **Non-integer scales are uneven.** `Font0` x1.5 (chat) gives letters with strokes of different widths, and `ç` is hard to read at that size. See `fonts.md`.
- **Passwords are shown in full on purpose** (`verify-cardputer-ui/references/screen-budget.md`): a tiny keyboard makes blind typing useless.

## Layout

- Header y=0..19; content from y=20; chat composer from y=106; footer text at or above y=123.
- **Pages show an indicator** HOME's pages are one continuous grid: moving off an edge lands on the same row of the next page.
- **A dead key is a bug.** A key that can't act says so: HOME's edge and Wi-Fi during local mode use `bumpHome`, a falling two-note tone and a red edge.
- **The 2x2 Home grid at `Font0` x2 tested well on the device.** Jay, on the first build with apps: "okay, menu worked well." A tile is 111 px wide, so a label is at most 9 characters at x2. Plan shorter French words, not smaller type.
- **Most secondary screens are still at `Font0` x1**, the smallest thing that can be drawn. That's history, not a decision to copy.

- **Font0 x4 for a number read across a room.** The clock apps' countdowns are Font0 x4 (28 px capitals): "7:00 AM" is 168 px, "99:59" 120 px. Status words under them are x2 and key hints x1 (`clock_apps` commit, 2026-11-07).
- **An overlay on the header is drawn last.** The screen-time warning first went in `drawHeader`, and chat then drew its room names over it ("Family" on top of "SCREEN: 5 MIN LEFT", runtime emulator shot, 2026-11-07). It now draws after the whole screen, in `render`.

- **Typed digits are a draft until confirmed.** v0.58's first alarm list set the alarm on every digit that made a time, so 7 over 7:00 AM showed 12:07 AM at once (Jay, 2026-11-10). The time now shows as typed, HH:MM from 00:00, says what Enter would set, and changes nothing until confirmed.

## Memory and time

- **The clock is seeded from the build time, so a plausible year proves nothing.** `ensureTime` used to call the clock synced when the year looked right, which the seed always makes true. Anything that acts on the time of day (the alarm) waits for SNTP's callback, `clockFromNtp` (2026-11-07).

- **The chip's clock drifts in light sleep; an alarm rings by a corrected one.** The ADV has no 32 kHz crystal, so light sleep counts on the internal oscillator. Albie's 07:00 alarm rang at 06:30 (2026-11-25, cause unproven). Since v0.67 a sleeping device asks the time servers at 05:00 and 5 minutes before each alarm. Anything else that must happen at a time of day while asleep needs the same check, and the runtime emulator's `clock-drift` to test it.

- **No PSRAM.** Lists and strings on a screen cost real heap beside the player: a 200-song list ran out of memory, and opening a card folder now stops playback for that reason (commit "whole library on the card").
- **Keep drawing off the key path.** Rendering is throttled to about 20 fps so typing and tones stay responsive (README). A long flourish must be stepped by time in the loop, never with `delay()`.

## Status marks (v0.57)

- **Three dots after a message read as text cut short.** The first "sending" mark was three grey dots at the right of the line; on the runtime emulator's shot, "first..." looked truncated. It is a small drawn clock now. Marks are drawn with lines, not a font: Font0 has no check mark that reads at x1.5 (runtime emulator shot `outbox-waiting.png`, 2026-11-07).
- **The header's network word is the travel mode, not the last status string.** Raw strings like `SYNC IO -5` and `ESPNOW ONL` told nobody whether a message would reach the server. The header now says ONLINE, NEARBY or OFFLINE, and STATUS page 1 gives the failed step at x2 so it can be read out over the phone (Jay, 2026-11-07: "so the kids or Jay can read it off").

## Lists and failures (v0.59)

- **A list that couldn't be read whole says so; it is never shown short.** The books list read the card with the core's `openNextFile`, which ends a folder at the first entry it can't allocate for. Away from home that showed Julien and Albie some of their books and no error, and the next String move from the failed allocation crashed the device (Jay, 2026-11-11; reproduced by the runtime emulator's `books-flaky`). A short read now shows `CARD NOT READ: LOW RAM` (`CARTE NON LUE: MEMOIRE`).
- **Failure texts come from the library and the player in English.** A French device showed them in English until `failureText` in `main.cpp` (v0.59). A new failure text gets its French line there.

## Idle and bedtime (v0.60)

- **A footer that names a mode collides with the mode's own label in text checks.** The players' footer says T SLEEP, so a scenario checking that the timer is gone must look for "SLEEP" with a time, not the word (runtime emulator `book-sleep`, 2026-11-13).
- **The big clock swallows the first key.** Anything that takes over the screen after idle gives the key that ends it to nobody, as the boot and charging quarantines do: a 1 pressed to wake must not open chat.
- **The alarm row holds four columns at most.** Time (x2, 104 px), MON-FRI, SAT-SUN and SOUND as 16 px cells centred at 126, 174 and 218, their x1 labels 6 px or more apart. A fifth needs a second line or a page, not narrower cells.

## Lyrics (v0.62)

- **The line being sung is read at a glance, so it is Font0 x2, never x1.** Two lines of 19 characters, broken at a space; the next line is x1 grey under it, the title x1 grey above with a 6 px bar, and the clock times give way. A line too long for two lines is cut with `~`, never shrunk (fonts.md: pick the size first). Shown only for songs on the card with a timed `.lrc`; without one the player is unchanged (runtime emulator `card-sync`, `lyrics.png`, 2026-11-21).
- **A letter key is the first letter of the word its hint shows, in the device's language.** L LYRICS / P PAROLES, F FAVOURITE / A AIME. Getting there took four tries on 2026-11-21: P on both languages, L for AIME, then F beside the word LIKE, then P beside SANS PAROLES (Jay). Check the letter against the word printed next to it on the screen, in both languages, not against a word in your head.

## HOME (v0.65)

- **A new app takes the place of the one used least, not a slot that shifts every tile after it.** CLOCK went beside TIMER and ALARM (Albie, 2026-11-22), and SCREEN moved to a fifth page of its own, so every other tile kept the position the kids' thumbs know. The last page may be short: its empty tiles aren't drawn, Up or Down onto one bumps, and Right from either row lands on its last tile.

## Brightness and places (v0.68)

- **A label one letter past a tile gets a shorter word, and the screen's header says it in full.** BRIGHTNESS is 10 letters; the tile is BRIGHT (LUMIERE) and the header BRIGHTNESS (LUMINOSITE). They filled HOME's short last page, so no tile moved.
- **A header title runs into the network word at x=106.** "AJOUTER A UN LIEU" (17 letters at x1) reached it on the runtime emulator's shot; a header holds 16 letters at most. Long titles go in the screen's first line instead (2026-11-25, `places-fr`).

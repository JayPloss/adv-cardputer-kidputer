# Kids' music: Spotify playlists to Navidrome and SD cards

A playlist of 20 songs or more also gets "TS - Top Secret Song" at its end: the song the startup melody comes from, ripped once by hand into the Wolfpack album with only that code name in its tags (no lyrics, no cover). Which song it is isn't written anywhere in this repo.

**Automatic since 2026-11-21:** `wolfpack-sync.py` runs steps 1 to 4 plus the lyrics on its own: it fetches
each playlist's Spotify page again, matches and rips only the new songs (a weak match waits in
`<data>/needs-a-look.txt` for a `FIX` entry in `rip.py`), copies them to ori-san, rescans Navidrome and makes
each kid's playlist again in Spotify's order; the Cardputers then fetch what they lack on home Wi-Fi.
A systemd user timer runs it every hour: `wolfpack-sync.service` and `.timer` here, linked into
`~/.config/systemd/user`. `journalctl --user -u wolfpack-sync` shows each run. Songs removed on Spotify leave
the Navidrome playlist, and from the next firmware after v0.61 the card moves them to `/music/.removed/<Kid>`
(hidden from the player, never deleted; v0.61 itself leaves them in place).

Run on paqman. Data (Spotify pages, matches, file map) lives in `~/.local/share/cardputer-music`
(`$CARDPUTER_MUSIC_DATA`), the MP3s in `~/Music/wolfpack`. Neither is in this repo.

1. Save each playlist's embed page as `spotify/<id>.html` in the data folder
   (`curl -sL -A Mozilla/5.0 https://open.spotify.com/embed/playlist/<id>`). The playlist's name is the kid's name.
2. `uv run --with ytmusicapi python -I resolve.py`: one YouTube Music match per song, scored on title,
   artist, length and explicit flag. Read the weak ones and add corrections to `FIX` in `rip.py`.
3. `uv run --with "yt-dlp[default]" --with yt-dlp-ejs --with mutagen python -I rip.py`: rips through
   `jay-ai-arsenal/tools/yt2phone.py`, tags from Spotify (album and album artist Wolfpack). Rerun to retry failures; done files are kept.
   Songs not on YouTube (Jay's own Suno songs) are tagged the same way by hand and added to `playlists.json` / `files.json`.
4. `rsync -a --chmod=F644 ~/Music/wolfpack/ root@ori-san-host:/srv/music/library/Wolfpack/`, then
   `navidrome-playlists.py scan`, and when `status` says it's done, `navidrome-playlists.py`: each kid's playlist, as that kid.
5. Card in the reader: `uv run --with mutagen python -I card.py <mount> <Kid>` copies the kid's list to `/music/<Kid>` as `001 Title - Artist.mp3`
   (the player sorts by name) with no ID3v2 tag (the player reads a tag as a broken frame: NOT AN MP3), and appends a dated entry to `CARTE-<KID>.txt`, the card's owner note and log.
   Rerunning copies only what's missing.
   Since v0.61 the Cardputer does this itself over Wi-Fi: it mirrors its owner's playlist (the Navidrome
   playlist named after the login) into `/music/<Playlist>` with the same names, fetching only what's
   missing, at most 4 songs a pass (passes 2 minutes apart, then every 5 minutes (one small request when the playlist is unchanged)), renaming a renumbered
   song, deleting nothing (after v0.61: songs off the list move to `/music/.removed/<Kid>`). Files keep Navidrome's ID3 tag: the v0.59 player reads past it. STATUS shows
   the state on its MUSIC line. So after step 4, a new song reaches the cards on home Wi-Fi with no card in the reader.
7. Lyrics: `uv run --with mutagen python -I lyrics.py` puts each song's lyrics in `<data>/lyrics/<file stem>.lrc`
   (LRCLIB's line times) or `.txt` (plain): LRCLIB first, `OTHER` for songs it files under another name or
   whose words come from the original (covers), Suno songs from their own tag, lyrics.ovh last. 2026-11-21:
   112 timed, 25 plain, 2 with none anywhere (Traverser les saisons, La Choucroute). `--report` counts.
6. `pkexec label-card CRD-DREW|CRD-EMMA|CRD-NAO` unmounts the card and sets its volume label; it refuses
   unless the matching `CARTE-<KID>.txt` is on the card. Root, so it opens the polkit dialog.

Papa's card (label CARDPUTERSD) holds the family library and books: none of this touches it.

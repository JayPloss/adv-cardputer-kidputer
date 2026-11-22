"""time-lyrics.py: the timing pass. Each Wolfpack song's lyrics aligned to our own recording.

Run with lyric-sync's environment:  uv run --project ~/Work/lyric-sync python -I time-lyrics.py [stem ...]
For every song with lyrics in <data>/lyrics (LRCLIB's .lrc, or plain .txt), lyric-sync separates the vocals
(Demucs), transcribes them and force-aligns the known words to them. A .lrc from LRCLIB belongs to the
original release, so its shift against our rip is measured first and each line is aligned inside its own
window; plain text is aligned line by line. The result goes to <data>/lyrics/timed/<stem>.lrc (line
times) with a .json beside it (word times, confidence, suspect lines); wolfpack-sync then puts the timed
file beside the song for Navidrome and the Cardputers. Done songs are skipped. Slow on a CPU: minutes a
song; the work cache (lyric-sync's WORK folder) keeps stems, so a rerun is quick.
"""
import hashlib, json, os, sys, time
from pathlib import Path

from lyric_sync import lyrics, process

DATA = Path(os.environ.get("CARDPUTER_MUSIC_DATA", Path.home() / ".local/share/cardputer-music"))
MUSIC = Path.home() / "Music" / "wolfpack"
WORK = Path(os.environ.get("LYRIC_SYNC_WORK", Path.home() / ".cache" / "wolfpack-timing"))
pl = json.loads((DATA / "playlists.json").read_text())
files = json.loads((DATA / "files.json").read_text())
out = DATA / "lyrics" / "timed"
out.mkdir(parents=True, exist_ok=True)


def songs():
    wanted = set(sys.argv[1:])
    for key, song in pl["songs"].items():
        stem = Path(files.get(key, "")).stem
        if not stem or (wanted and stem not in wanted):
            continue
        audio = MUSIC / f"{stem}.mp3"
        lrc, txt = DATA / "lyrics" / f"{stem}.lrc", DATA / "lyrics" / f"{stem}.txt"
        if audio.exists() and (lrc.exists() or txt.exists()):
            yield stem, song, audio, lrc, txt


def main():
    todo = [s for s in songs() if not (out / f"{s[0]}.lrc").exists()]
    print(f"[time-lyrics] {len(todo)} songs to time", flush=True)
    for n, (stem, song, audio, lrc, txt) in enumerate(todo, 1):
        started = time.time()
        spec = {"song_id": hashlib.sha1(stem.encode()).hexdigest()[:16], "artist": song["artists"], "title": song["title"],
                "duration": song["ms"] / 1000}
        if lrc.exists():
            spec["lrclib"] = {"syncedLyrics": lrc.read_text(), "plainLyrics": txt.read_text() if txt.exists() else None}
        else:
            spec["custom_text"] = txt.read_text()
        try:
            result = process.compute(spec, audio, WORK / spec["song_id"])
        except Exception as error:  # one bad song doesn't stop the pass
            print(f"[time-lyrics] {n}/{len(todo)} FAILED {stem}: {error}", flush=True)
            continue
        doc = result["doc"]
        (out / f"{stem}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        (out / f"{stem}.lrc").write_text(lyrics.to_lrc(doc, words=False, title=song["title"], artist=song["artists"]))
        suspects = sum(1 for line in doc.get("lines", []) if line.get("suspect"))
        print(f"[time-lyrics] {n}/{len(todo)} {stem}: {doc.get('source')}, shift {doc.get('lrc_shift')}, "
              f"{len(doc.get('lines', []))} lines, {suspects} suspect, {time.time() - started:.0f}s", flush=True)


main()

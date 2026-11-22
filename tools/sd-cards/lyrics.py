"""lyrics.py: lyrics for every Wolfpack song, from LRCLIB (lrclib.net, free, no key).

Run: python3 -I lyrics.py            (writes <data>/lyrics/<song file stem>.lrc and/or .txt)
     python3 -I lyrics.py --report   (what's there, what's missing)

LRCLIB answers by artist, title and length (/api/get), else by search (/api/search, a match within
4 seconds of the song's length). A synced result goes to .lrc (line times: the timing pass starts from
these), a plain one to .txt. A song LRCLIB lacks falls back to the lyrics
in its own MP3 tag (USLT): Suno writes them into every download, so Jay's songs (Jaybird and the
Tokenizers) come from there, with section labels ([Verse 1], *(Couplet 1)*) taken out since nobody
sings them. Run with mutagen: uv run --with mutagen python -I lyrics.py. Songs already done are
skipped; delete a file to fetch it again.
"""
import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

data = Path(os.environ.get("CARDPUTER_MUSIC_DATA", Path.home() / ".local/share/cardputer-music"))
pl = json.loads((data / "playlists.json").read_text())
files = json.loads((data / "files.json").read_text())
out = data / "lyrics"
out.mkdir(exist_ok=True)
AGENT = "cardputer-wolfpack-lyrics (github.com/JayPloss/adv-cardputer-kidputer)"


def lrclib(path, **query):
    url = f"https://lrclib.net/api/{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def find(song):
    artist = song["artists"].split(", ")[0].replace("\xa0", " ")
    seconds = round(song["ms"] / 1000)
    hit = lrclib("get", artist_name=artist, track_name=song["title"], duration=seconds)
    if hit and (hit.get("syncedLyrics") or hit.get("plainLyrics")):
        return hit
    found = lrclib("search", track_name=song["title"], artist_name=artist) or []
    close = [h for h in found if abs((h.get("duration") or 0) - seconds) <= 4 and (h.get("syncedLyrics") or h.get("plainLyrics"))]
    close.sort(key=lambda h: (not h.get("syncedLyrics"), abs(h["duration"] - seconds)))
    return close[0] if close else None


# Songs LRCLIB files under another artist or title. A cover's words come from the original, plain only:
# its line times belong to the other recording.
OTHER = {
    "Cook Da Beatz - TU T'RAPPELLES": ("Cook Da Beatz", "TU T'RAPPELLES", True),
    "Saja Boys - Ton sauveur": ("Saja Boys", "Ton sauveur", True),
    "Gabriel Fredette - À deux": ("Jacques Brel", "À deux", False),
    "Spondy_Hsd - Dévoile": ("HUNTR/X", "Dévoile", False),
    "Vernis Rouge - Comme des loups - Version Acoustique": ("Vernis Rouge", "Comme des loups  (Version Acoustique)", True),
    "Jeck - Tous les cris les S.O.S": ("Les Enfoirés", "Tous les cris les S.O.S.", False),
    "Jeck - SOS d'un terrien en détresse": ("Calogero", "SOS d’un terrien en détresse", False),
}


def other(stem):
    artist, title, synced = OTHER[stem]
    hits = lrclib("search", track_name=title, artist_name=artist) or []
    hits = [h for h in hits if h.get("plainLyrics") and h["trackName"].lower() == title.lower()]
    if not hits:
        return None
    hit = dict(hits[0])
    if not synced:
        hit["syncedLyrics"] = None
    return hit


def ovh(song):
    """lyrics.ovh, plain text only, for what LRCLIB doesn't have at all."""
    artist = song["artists"].split(", ")[0]
    url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(song['title'])}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=20) as reply:
            return {"plainLyrics": json.load(reply).get("lyrics", "").replace("\r\n", "\n")}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def tagged(path):
    """The lyrics in the MP3's own tag, without section labels; '' when there are none."""
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        frames = ID3(path).getall("USLT")
    except ID3NoHeaderError:
        return ""
    text = max((f.text for f in frames), key=len, default="")
    keep = []
    for line in text.splitlines():
        bare = line.strip().strip("*").strip()
        if bare.startswith(("[", "(")) and bare.endswith(("]", ")")) and len(bare) < 40:
            continue
        keep.append(line.rstrip())
    return "\n".join(keep).strip()


def main():
    report = sys.argv[1:] == ["--report"]
    counts = {"synced": 0, "plain": 0, "instrumental": 0, "missing": 0}
    missing = []
    for key, song in pl["songs"].items():
        stem = Path(files.get(key, f"{song['artists']} - {song['title']}.mp3")).stem
        lrc, txt, none = out / f"{stem}.lrc", out / f"{stem}.txt", out / f"{stem}.instrumental"
        if not report and not (lrc.exists() or txt.exists() or none.exists()):
            hit = None
            if "Tokenizers" not in song["artists"]:
                hit = other(stem) if stem in OTHER else find(song)
                if not hit:
                    hit = ovh(song)
                time.sleep(0.5)
                if hit and hit.get("instrumental"):
                    none.write_text("")
                elif hit:
                    if hit.get("syncedLyrics"):
                        lrc.write_text(hit["syncedLyrics"].strip() + "\n")
                    if hit.get("plainLyrics"):
                        txt.write_text(hit["plainLyrics"].strip() + "\n")
            mp3 = Path.home() / "Music" / "wolfpack" / files.get(key, "")
            if not (lrc.exists() or txt.exists() or none.exists()) and mp3.is_file() and (words := tagged(mp3)):
                txt.write_text(words + "\n")
        state = "synced" if lrc.exists() else "plain" if txt.exists() else "instrumental" if none.exists() else "missing"
        counts[state] += 1
        if state == "missing":
            missing.append(stem)
    print(", ".join(f"{n} {k}" for k, n in counts.items()), f"of {len(pl['songs'])}")
    for stem in missing:
        print("  missing:", stem)


main()

"""wolfpack-sync.py: the kids' Spotify playlists, followed all the way to their Cardputers.

Run on paqman (it holds the data, the keyring and the rip tools): python3 -I wolfpack-sync.py
A systemd user timer runs it every 6 hours (wolfpack-sync.timer, next to this file).

1. Each playlist's Spotify embed page again (DATA/spotify/<id>.html; a failed fetch keeps the old page).
2. resolve.py: playlists.json in Spotify's order, hand-added songs kept at the end; a YouTube Music
   match for each new song only.
3. rip.py: new songs into ~/Music/wolfpack; a weak match waits in DATA/needs-a-look.txt.
4. lyrics.py: lyrics for the new songs, copied beside each song as Navidrome's sidecar (.lrc timed, else .txt).
5. When anything changed: rsync to ori-san's library, a Navidrome scan, and each kid's playlist made
   again in the new order (navidrome-playlists.py). Each Cardputer then fetches what it lacks on home
   Wi-Fi (firmware v0.61 card sync).
Prints one line per step and a summary; exits 1 if a step failed. Navidrome is reached on the home
network, else through the tailnet.
"""
import hashlib, json, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CARDPUTER_MUSIC_DATA", Path.home() / ".local/share/cardputer-music"))
MUSIC = Path.home() / "Music" / "wolfpack"
NAVIDROME = [("192.168.0.235", 4533), ("100.91.108.85", 4533)]  # home network, then ori-san on the tailnet
LIBRARY = "root@ori-san-host:/srv/music/library/Wolfpack/"


def step(name, command, **kw):
    print(f"[wolfpack-sync] {name}", flush=True)
    done = subprocess.run(command, cwd=HERE, text=True, capture_output=True, **kw)
    tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
    for line in tail:
        print(f"    {line}", flush=True)
    if done.returncode:
        raise SystemExit(f"[wolfpack-sync] {name} FAILED ({done.returncode})")
    return done.stdout


def fingerprint():
    state = {"lists": json.loads((DATA / "playlists.json").read_text())["lists"],
             "files": sorted(p.name for p in MUSIC.glob("*.mp3")),
             "lyrics": sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in list(MUSIC.glob("*.lrc")) + list(MUSIC.glob("*.txt")))}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def navidrome():
    for host, port in NAVIDROME:
        try:
            socket.create_connection((host, port), timeout=3).close()
            return f"http://{host}:{port}"
        except OSError:
            continue
    raise SystemExit("[wolfpack-sync] Navidrome not reachable")


def main():
    before = fingerprint()
    print("[wolfpack-sync] Spotify pages", flush=True)
    for page in sorted((DATA / "spotify").glob("*.html")):
        request = urllib.request.Request(f"https://open.spotify.com/embed/playlist/{page.stem}", headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                body = reply.read().decode()
            if "__NEXT_DATA__" in body:
                page.write_text(body)
            else:
                print(f"    {page.stem}: no playlist in the page, kept the old one")
        except OSError as error:
            print(f"    {page.stem}: {error}, kept the old one")
    step("matches for new songs", ["uv", "run", "-q", "--with", "ytmusicapi", "python", "-I", "resolve.py"])
    step("rip new songs", ["uv", "run", "-q", "--with", "yt-dlp[default]", "--with", "yt-dlp-ejs", "--with", "mutagen", "python", "-I", "rip.py"])
    look = (DATA / "needs-a-look.txt").read_text().strip() if (DATA / "needs-a-look.txt").exists() else ""
    step("lyrics", ["uv", "run", "-q", "--with", "mutagen", "python", "-I", "lyrics.py"])
    # Each song's lyrics beside it, as Navidrome's sidecar (same name, .lrc or .txt): it serves them through
    # getLyricsBySongId, and the Cardputers fetch them from there. A timed file from the timing pass
    # (lyrics/timed/) wins over LRCLIB's.
    print("[wolfpack-sync] lyrics beside the songs", flush=True)
    for source in sorted((DATA / "lyrics").glob("*.lrc")) + sorted((DATA / "lyrics").glob("*.txt")):
        timed = DATA / "lyrics" / "timed" / f"{source.stem}.lrc"
        song = MUSIC / f"{source.stem}.mp3"
        if not song.exists():
            continue
        if source.suffix == ".txt" and ((DATA / "lyrics" / f"{source.stem}.lrc").exists() or timed.exists()):
            continue
        chosen = timed if timed.exists() else source
        target = MUSIC / f"{source.stem}{chosen.suffix}"
        if not target.exists() or target.read_bytes() != chosen.read_bytes():
            target.write_bytes(chosen.read_bytes())
    if fingerprint() == before and "--force" not in sys.argv:
        print("[wolfpack-sync] nothing changed on Spotify")
    else:
        step("copy to ori-san", ["rsync", "-a", "--chmod=F644", "--exclude=.tmp", f"{MUSIC}/", LIBRARY])
        env = {**os.environ, "NAVIDROME_URL": navidrome()}
        step("Navidrome scan", ["python3", "-I", "navidrome-playlists.py", "scan"], env=env)
        for _ in range(60):
            time.sleep(5)
            if "'scanning': False" in step("scan status", ["python3", "-I", "navidrome-playlists.py", "status"], env=env):
                break
        step("kids' playlists", ["python3", "-I", "navidrome-playlists.py"], env=env)
    if look:
        print("[wolfpack-sync] needs a look (not ripped):")
        for line in look.splitlines():
            print(f"    {line}")
    print("[wolfpack-sync] done")


main()

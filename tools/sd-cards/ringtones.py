"""ringtones.py: alarm sounds for the Cardputers, from the Android Open Source Project (Apache License 2.0,
frameworks/base NOTICE), as a shared Navidrome playlist "Sonneries".

Run on paqman: python3 -I ringtones.py
Downloads each sound from android.googlesource.com (gitiles ?format=TEXT, base64), makes it an MP3 the
Cardputer plays (mono, 44.1 kHz, 128 kbps, a plain ID3v2.3 tag the player reads past since v0.59), puts it in ~/Music/sonneries, copies that to
ori-san's library (Sonneries/), rescans Navidrome and makes the playlist "Sonneries" as admin, public, so
every account sees it. The Cardputers' card sync mirrors it into /music/Sonneries, where the alarm's
sound picker lists it. Rerun: done files are kept.
"""
import base64, os, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path.home() / "Music" / "sonneries"
GITILES = "https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/data/sounds"
# Alarm sounds first (the clock app's own), then the classic beeps and the rooster.
ALARMS = ["Argon", "Barium", "Carbon", "Cesium", "Copernicium", "Curium", "Fermium", "Hassium", "Helium", "Krypton",
          "Neon", "Neptunium", "Nobelium", "Osmium", "Oxygen", "Platinum", "Plutonium", "Promethium", "Scandium",
          "Analysis", "Departure", "FireDrill", "Timing"]
CLASSIC = ["Alarm_Classic", "Alarm_Beep_01", "Alarm_Beep_02", "Alarm_Beep_03", "Alarm_Buzzer", "Alarm_Rooster_01", "Alarm_Rooster_02"]
LIBRARY = "root@ori-san-host:/srv/music/library/Sonneries/"
NAVIDROME = os.environ.get("NAVIDROME_URL", "http://192.168.0.235:4533")


def fetch(path):
    with urllib.request.urlopen(f"{GITILES}/{path}?format=TEXT", timeout=30) as reply:
        return base64.b64decode(reply.read())


def title(name):
    return name.replace("Alarm_", "").replace("_0", " ").replace("_", " ").replace("FireDrill", "Fire Drill")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    names = [(f"alarms/ogg/{n}.ogg", n) for n in ALARMS] + [(f"{n}.ogg", n) for n in CLASSIC]
    for path, name in names:
        mp3 = OUT / f"{title(name)}.mp3"
        if mp3.exists():
            continue
        ogg = OUT / f".{name}.ogg"
        ogg.write_bytes(fetch(path))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(ogg), "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k",
                        "-id3v2_version", "3", "-write_xing", "0", "-metadata", f"title={title(name)}", "-metadata", "artist=Android",
                        "-metadata", "album=Sonneries", str(mp3)], check=True)
        ogg.unlink()
        print(f"  {mp3.name}")
    subprocess.run(["rsync", "-a", "--chmod=F644", "--exclude=.*", f"{OUT}/", LIBRARY], check=True)
    sys.path.insert(0, str(HERE))
    env = {**os.environ, "NAVIDROME_URL": NAVIDROME}
    subprocess.run(["python3", "-I", str(HERE / "navidrome-playlists.py"), "scan"], env=env, check=True)
    for _ in range(60):  # until Navidrome has finished the scan
        time.sleep(5)
        status = subprocess.run(["python3", "-I", str(HERE / "navidrome-playlists.py"), "status"], env=env, capture_output=True, text=True).stdout
        if "'scanning': False" in status:
            break
    import hashlib, json, secrets, urllib.parse
    def call(user, method, params):
        pw = subprocess.run(["secret-tool", "lookup", "service", "navidrome", "account", user], capture_output=True, text=True).stdout.strip()
        salt = secrets.token_hex(6)
        q = [("u", user), ("t", hashlib.md5((pw + salt).encode()).hexdigest()), ("s", salt), ("v", "1.16.1"), ("c", "ringtones"), ("f", "json")] + params
        with urllib.request.urlopen(f"{NAVIDROME}/rest/{method}?{urllib.parse.urlencode(q)}", timeout=60) as r:
            d = json.load(r)["subsonic-response"]
        if d["status"] != "ok": raise SystemExit(f"{method}: {d}")
        return d
    albums = call("admin", "search3", [("query", "Sonneries"), ("songCount", "0"), ("artistCount", "0"), ("albumCount", "20")]).get("searchResult3", {}).get("album", [])
    songs = [t for a in albums if a["name"] == "Sonneries" for t in call("admin", "getAlbum", [("id", a["id"])])["album"].get("song", [])]
    order = [title(n) for _, n in names]
    songs.sort(key=lambda t: order.index(t["title"]) if t["title"] in order else 99)
    for p in call("admin", "getPlaylists", []).get("playlists", {}).get("playlist", []):
        if p["name"] == "Sonneries" and p["owner"] == "admin": call("admin", "deletePlaylist", [("id", p["id"])])
    made = call("admin", "createPlaylist", [("name", "Sonneries")] + [("songId", t["id"]) for t in songs])
    call("admin", "updatePlaylist", [("playlistId", made["playlist"]["id"]), ("public", "true")])
    print(f"Sonneries: {len(songs)} sounds, public")


main()

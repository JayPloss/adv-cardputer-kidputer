#pragma once

struct SupaChatChangelogEntry {
  const char *version;
  const char *lines[8];
  int lineCount;
};

// Canonical on-device release notes. The emulator reads this file directly so
// its Changelog screen cannot silently drift from the firmware.
constexpr SupaChatChangelogEntry kSupaChatChangelog[] = {
  {"v0.65", {"CLOCK on Home, by TIMER", "SCREEN moved to the last page", "Messages arrive during sync"}, 3},
  {"v0.64", {"Songs with lyrics in blue", "Check: on your play list", "RIGHT adds, LEFT removes", "G0 goes back", "Long names scroll", "Start: READY, then a key", "Fn+J: large type for Papa"}, 7},
  {"v0.63", {"Messages send while syncing", "Full outbox: sent nearby", "Phone hearts and faces", "Lyrics within minutes", "New songs checked often"}, 5},
  {"v0.62", {"Lyrics: L (P in French)", "Shuffle M, repeat R, delete D", "New songs told in chat", "Ringtones for alarms", "Songs off the list leave", "No impossible alarm times", "Sleeps after 10 idle min", "Fast Wi-Fi: no restart"}, 8},
  {"v0.61", {"Emoji show as pictures", "New songs come over Wi-Fi", "STATUS: music sync line", "Battery % keeps up charging"}, 4},
  {"v0.60", {"Big clock after 2 min idle", "Sleep timer: T in players", "Books at bedtime: 30 min", "Alarm can play a song", "School nights: book off 21:30"}, 5},
  {"v0.59", {"Every book on the card shows", "No crash listing at low RAM", "MP3s with cover art play", "VBR songs: right length", "Says why a file won't play"}, 5},
  {"v0.58", {"Up to four alarms", "Weekday and weekend switches", "Fixed: a play could be lost", "French keyboard: CSA layout", "Tab in chat: French keys", "Alarm time typed from 00:00", "Hints name the real exit key"}, 7},
  {"v0.57", {"Unsent texts kept and retried", "Sent/delivered/read marks", "ONLINE or NEARBY shown", "STATUS names the failure", "Survives cert renewals", "Clearer nearby walkie", "Nearby texts confirmed"}, 7},
  {"v0.56", {"Timer and stopwatch", "Alarm clock", "Screen time countdown"}, 3},
  {"v0.55", {"Settings moved to Home", "Pages of apps on Home", "Bump at the last page"}, 3},
  {"v0.54", {"Home screen with apps", "Music player", "Audiobook player", "Current server pin"}, 4},
  {"v0.53", {"Reliable Fox peer discovery", "Shared ESP-NOW hunt channel", "Newest-first history pages", "Compact device sync payload", "Unified feature release"}, 5},
  {"v0.52", {"Newest-first history pages", "Compact device sync payload", "Large-room JSON fix"}, 3},
  {"v0.51", {"Simpler charging screen", "20-second screen timeout", "Reduced charging redraws"}, 3},
  {"v0.50", {"Papa custom boot art", "Mama build + boot art"}, 2},
  {"v0.49", {"Low-power charging mode", "Charge history plot", "Battery event logging", "Boot key quarantine", "Verbose sync diagnostics"}, 5},
  {"v0.48", {"Scrollable build changes", "Working language controls", "Reliable room switching", "Zero-copy history sync", "Fixed cable detection", "Clean battery digits", "Voice / walkie split"}, 7},
  {"v0.47", {"Paged menus", "Build changelog", "ESP-NOW local only", "Language menu fix"}, 4},
  {"v0.46", {"Groups own rooms", "EN/FR preferences", "Automatic group language", "QR group invites"}, 4},
  {"v0.45", {"Contextual arrows", "Correct French glyphs", "Fallback WiFi profiles", "Legacy mesh key fix"}, 4},
  {"v0.44", {"French accents", "Complete key chorus", "Original startup song", "Bilingual web UI"}, 4},
  {"v0.43", {"History + notices", "Sender names + colours", "ESP-NOW fallback", "Complete startup melody"}, 4},
  {"v0.42", {"Wolfpack terminals", "Emma, Naomie, Andrew", "Voice + local replay", "Startup audio fixes"}, 4},
};
constexpr int kSupaChatChangelogCount = sizeof(kSupaChatChangelog) / sizeof(kSupaChatChangelog[0]);

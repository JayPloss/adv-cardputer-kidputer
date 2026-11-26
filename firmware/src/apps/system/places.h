#pragma once
// Places (v0.68): the device recognises where it is by the Wi-Fi access points around it, as Ben's
// phone does (jay-ai-arsenal ben-app PlaceMatch.kt, ported). A place is named by the person holding the
// device: SAVE THIS PLACE makes a new one from a scan; ADD TO A PLACE makes a named part of one (a
// sub-place: HOME / PORCH). Each holds up to kMaxPrints prints, one scan each. Plain C++, no Arduino,
// so the host test (firmware/tools/places-test.cpp) runs it as the device does.
#include <stdint.h>
#include <string.h>

namespace places {

constexpr int kMaxPlaces = 12;      // places and sub-places together
constexpr int kMaxPrints = 2;       // scans kept per place
constexpr int kPrintAps = 10;       // the strongest access points of a scan
constexpr int kNameBytes = 32;      // UTF-8, so a 16-letter French name with accents fits
constexpr int kNameLetters = 15;

// Signals at or below this are noise for matching; at -35 dBm and above an access point weighs 1.
constexpr float kFloorDbm = -92.0f;
constexpr float kTopDbm = -35.0f;
// Enter a place at this score or above, stay in it down to kStay. A sub-place needs kRoomEnter, must
// beat its siblings by kRoomMargin, and must beat its parent's own prints (otherwise it is the place).
constexpr float kEnter = 0.32f;
constexpr float kStay = 0.20f;
constexpr float kRoomEnter = 0.30f;
constexpr float kRoomMargin = 0.05f;

// One access point: its key (the BSSID with the locally administered bit cleared and the last hex
// digit dropped, so a router's 2.4 GHz, guest and main networks count once) and its signal.
struct Ap {
  uint8_t key[6];
  int8_t rssi;
};

struct Print {
  uint8_t count = 0;
  Ap aps[kPrintAps];
};

struct Place {
  char name[kNameBytes] = {0};  // empty: a free slot
  int8_t parent = -1;            // a sub-place's place, else -1
  uint8_t prints = 0;
  bool used() const { return name[0] != 0; }
};

inline void apKey(const uint8_t bssid[6], uint8_t key[6]) {
  memcpy(key, bssid, 6);
  key[0] &= static_cast<uint8_t>(~0x02);
  key[5] &= 0xF0;
}

inline bool sameKey(const uint8_t a[6], const uint8_t b[6]) { return memcmp(a, b, 6) == 0; }

inline float weight(float rssi) {
  const float w = (rssi - kFloorDbm) / (kTopDbm - kFloorDbm);
  return w < 0 ? 0 : w > 1 ? 1 : w;
}

// Phones and travel routers move with people: they say nothing about a place.
inline bool isMobileHotspot(const char *ssid) {
  if (!ssid || !*ssid) return false;
  static const char *const kWords[] = {"iphone", "android", "galaxy", "pixel", "'s phone", "hotspot", "mifi", "jetpack", "direct-"};
  char lower[40];
  size_t n = 0;
  for (; ssid[n] && n + 1 < sizeof(lower); n++) lower[n] = (ssid[n] >= 'A' && ssid[n] <= 'Z') ? ssid[n] + 32 : ssid[n];
  lower[n] = 0;
  for (const char *word : kWords) if (strstr(lower, word)) return true;
  return false;
}

// A scan reduced to a print: one entry per access point key at its strongest, noise and hotspots left
// out, the strongest kPrintAps kept.
struct ScanBuilder {
  Print print;
  void add(const uint8_t bssid[6], int rssi, const char *ssid) {
    if (rssi <= kFloorDbm || isMobileHotspot(ssid)) return;
    uint8_t key[6];
    apKey(bssid, key);
    for (int i = 0; i < print.count; i++)
      if (sameKey(print.aps[i].key, key)) { if (rssi > print.aps[i].rssi) print.aps[i].rssi = static_cast<int8_t>(rssi); return; }
    if (print.count < kPrintAps) { memcpy(print.aps[print.count].key, key, 6); print.aps[print.count].rssi = static_cast<int8_t>(rssi); print.count++; return; }
    int weakest = 0;
    for (int i = 1; i < print.count; i++) if (print.aps[i].rssi < print.aps[weakest].rssi) weakest = i;
    if (rssi > print.aps[weakest].rssi) { memcpy(print.aps[weakest].key, key, 6); print.aps[weakest].rssi = static_cast<int8_t>(rssi); }
  }
};

// Weighted Tanimoto (Ruzicka) similarity, 0..1: the sum of the smaller weights over the sum of the larger
// ones, across every access point in either. Shared strong access points raise it; strong ones in only one
// lower it; faint ones barely count.
inline float score(const Print &scan, const Print &print) {
  if (!scan.count || !print.count) return 0;
  float num = 0, den = 0;
  for (int i = 0; i < scan.count; i++) {
    const float x = weight(scan.aps[i].rssi);
    float y = 0;
    for (int j = 0; j < print.count; j++) if (sameKey(scan.aps[i].key, print.aps[j].key)) { y = weight(print.aps[j].rssi); break; }
    num += x < y ? x : y;
    den += x > y ? x : y;
  }
  for (int j = 0; j < print.count; j++) {
    bool shared = false;
    for (int i = 0; i < scan.count && !shared; i++) shared = sameKey(scan.aps[i].key, print.aps[j].key);
    if (!shared) den += weight(print.aps[j].rssi);
  }
  return den > 0 ? num / den : 0;
}

// Where a scan is: the place (or -1) and, inside it, the sub-place (or -1). `current` is the place it was
// in, which it stays in down to kStay. `prints(index, which, out)` reads a stored print.
struct Where {
  int place = -1;
  int sub = -1;
  float placeScore = 0;
  float subScore = 0;
};

template <typename ReadPrint>
Where recognise(const Place (&list)[kMaxPlaces], const Print &scan, int current, ReadPrint prints) {
  float own[kMaxPlaces] = {0};
  for (int index = 0; index < kMaxPlaces; index++) {
    if (!list[index].used()) continue;
    Print print;
    for (int which = 0; which < list[index].prints; which++)
      if (prints(index, which, print)) { const float s = score(scan, print); if (s > own[index]) own[index] = s; }
  }
  // A place scores its own prints and its sub-places': the porch is part of home.
  Where where;
  for (int index = 0; index < kMaxPlaces; index++) {
    if (!list[index].used() || list[index].parent >= 0) continue;
    float total = own[index];
    for (int child = 0; child < kMaxPlaces; child++)
      if (list[child].used() && list[child].parent == index && own[child] > total) total = own[child];
    const float needed = index == current ? kStay : kEnter;
    if (total >= needed && total > where.placeScore) { where.place = index; where.placeScore = total; }
  }
  if (where.place < 0) return where;
  float best = 0, second = 0;
  int bestChild = -1;
  for (int child = 0; child < kMaxPlaces; child++) {
    if (!list[child].used() || list[child].parent != where.place) continue;
    if (own[child] > best) { second = best; best = own[child]; bestChild = child; }
    else if (own[child] > second) second = own[child];
  }
  if (bestChild >= 0 && best >= kRoomEnter && best - second >= kRoomMargin && best > own[where.place]) {
    where.sub = bestChild; where.subScore = best;
  }
  return where;
}

// The first free slot, or -1 when all kMaxPlaces are used.
inline int freeSlot(const Place (&list)[kMaxPlaces]) {
  for (int index = 0; index < kMaxPlaces; index++) if (!list[index].used()) return index;
  return -1;
}

// A name already used by a place with the same parent (names are compared as typed).
inline bool nameTaken(const Place (&list)[kMaxPlaces], const char *name, int parent) {
  for (int index = 0; index < kMaxPlaces; index++)
    if (list[index].used() && list[index].parent == parent && strcmp(list[index].name, name) == 0) return true;
  return false;
}

}  // namespace places

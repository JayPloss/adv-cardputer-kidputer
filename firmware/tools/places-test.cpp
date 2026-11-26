// Host test of place recognition (firmware/src/apps/system/places.h).
//   g++ -std=c++11 -I firmware/src firmware/tools/places-test.cpp -o /tmp/places-test && /tmp/places-test
#include <cassert>
#include <cstdio>
#include "apps/system/places.h"

using namespace places;

static Print scanOf(const int (*aps)[2], int count) {
  ScanBuilder builder;
  for (int i = 0; i < count; i++) {
    const uint8_t bssid[6] = {0x24, 0x0a, 0xc4, 0x00, static_cast<uint8_t>(aps[i][0]), 0x10};
    builder.add(bssid, aps[i][1], "net");
  }
  return builder.print;
}

int main() {
  // A house: its router and two neighbours. Inside: the router strong. On the porch: the router weaker,
  // the neighbour across the street stronger, and a third neighbour only seen outside.
  const int inside[][2] = {{1, -45}, {2, -78}, {3, -84}};
  const int porch[][2] = {{1, -68}, {2, -60}, {3, -80}, {4, -74}};
  const int inside2[][2] = {{1, -48}, {2, -80}, {3, -86}};
  const int porch2[][2] = {{1, -70}, {2, -58}, {3, -82}, {4, -72}};
  const int school[][2] = {{9, -50}, {10, -60}, {11, -70}};

  Place list[kMaxPlaces];
  Print stored[kMaxPlaces][kMaxPrints];
  auto read = [&](int index, int which, Print &out) { out = stored[index][which]; return true; };
  strcpy(list[0].name, "HOME"); list[0].prints = 1; stored[0][0] = scanOf(inside, 3);
  strcpy(list[1].name, "PORCH"); list[1].parent = 0; list[1].prints = 1; stored[1][0] = scanOf(porch, 4);

  Where w = recognise(list, scanOf(inside2, 3), -1, read);
  printf("inside: place %d %.2f sub %d\n", w.place, w.placeScore, w.sub);
  assert(w.place == 0 && w.sub == -1);
  w = recognise(list, scanOf(porch2, 4), -1, read);
  printf("porch: place %d %.2f sub %d %.2f\n", w.place, w.placeScore, w.sub, w.subScore);
  assert(w.place == 0 && w.sub == 1);
  w = recognise(list, scanOf(school, 3), 0, read);
  assert(w.place == -1 && w.sub == -1);

  // Key folding: the same radio's guest network (locally administered bit, last digit) counts once.
  ScanBuilder builder;
  const uint8_t main[6] = {0x24, 0x0a, 0xc4, 0x12, 0x34, 0x5f};
  const uint8_t guest[6] = {0x26, 0x0a, 0xc4, 0x12, 0x34, 0x50};
  builder.add(main, -60, "Home"); builder.add(guest, -55, "Home-guest");
  assert(builder.print.count == 1 && builder.print.aps[0].rssi == -55);
  // Phones' hotspots and noise say nothing about a place.
  const uint8_t phone[6] = {0x40, 0, 0, 0, 1, 0};
  builder.add(phone, -40, "Albie's iPhone"); builder.add(phone, -95, "x");
  assert(builder.print.count == 1);
  // The strongest kPrintAps are kept.
  ScanBuilder many;
  for (int i = 0; i < 15; i++) { const uint8_t b[6] = {0x24, 0, 0, 0, static_cast<uint8_t>(i), 0}; many.add(b, -50 - i * 2, "n"); }
  assert(many.print.count == kPrintAps);
  for (int i = 0; i < many.print.count; i++) assert(many.print.aps[i].rssi >= -50 - 9 * 2);

  // Identical scans score 1; slots and names.
  assert(score(scanOf(inside, 3), scanOf(inside, 3)) > 0.99f);
  assert(freeSlot(list) == 2 && nameTaken(list, "PORCH", 0) && !nameTaken(list, "PORCH", -1));
  printf("places_test=PASS\n");
  return 0;
}

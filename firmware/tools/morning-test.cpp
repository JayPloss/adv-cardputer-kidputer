// Host test of the Morning Overview's rules (docs/morning-overview.md, part B).
//   g++ -std=c++11 -I firmware/src firmware/tools/morning-test.cpp -o /tmp/morning-test && /tmp/morning-test
#include <cassert>
#include <cstdio>
#include <string>
#include "apps/time/morning.h"

using namespace morning;

static std::vector<std::string> lines(const char *text, int width) {
  std::vector<std::string> out;
  for (const Range &range : wrap(text, width)) out.push_back(std::string(text + range.start, text + range.end));
  return out;
}

int main() {
  // Trigger: snooze plays it; the stop after the snooze opens it without playing again; next day plays again.
  Trigger trigger;
  Action action = onAlarmHandled(trigger, 300, 7);
  assert(action.open && action.play);
  action = onAlarmHandled(trigger, 300, 7);
  assert(action.open && !action.play);
  action = onAlarmHandled(trigger, 301, 6);
  assert(action.open && action.play);
  // An afternoon or night alarm isn't a morning: nothing opens, and it doesn't use up the morning.
  Trigger afternoon;
  action = onAlarmHandled(afternoon, 300, 15); assert(!action.open && !action.play);
  action = onAlarmHandled(afternoon, 300, 3); assert(!action.open);
  action = onAlarmHandled(afternoon, 300, 4); assert(action.open && action.play);
  action = onAlarmHandled(afternoon, 300, 10); assert(action.open && !action.play);
  assert(!isMorningAlarm(11));

  // Which brief: today's; tomorrow's not yet; the newest before today; any newest when the date is unknown.
  std::vector<std::string> dates = {"2026-11-24", "2026-11-26", "2026-11-25", "junk"};
  assert(pickBrief(dates, "2026-11-25") == 2);
  assert(pickBrief(dates, "2026-11-27") == 1);
  assert(pickBrief({"2026-11-26"}, "2026-11-25") == -1);
  assert(pickBrief(dates, nullptr) == 1);
  assert(pickBrief(dates, "") == 1);
  assert(pickBrief({}, "2026-11-25") == -1);
  assert(expired("2026-11-21", "2026-11-24") && !expired("2026-11-22", "2026-11-24"));
  assert(expired("2026-12-29", "2027-01-01") && !expired("2026-12-31", "2027-01-01"));

  // Wrapping at 19 characters, by character not byte, at spaces.
  std::vector<std::string> wrapped = lines("Il fait moins huit, il vente et il neige. Été éàç.", 19);
  assert(wrapped.size() == 3);
  assert(wrapped[0] == "Il fait moins huit,");
  assert(wrapped[1] == "il vente et il");
  assert(wrapped[2] == "neige. Été éàç.");
  // A word longer than a line is cut; newlines break; no empty trailing line.
  wrapped = lines("Supercalifragilistique ok\nfin", 10);
  assert(wrapped.size() == 4 && wrapped[0] == "Supercalif" && wrapped[1] == "ragilistiq" && wrapped[2] == "ue ok" && wrapped[3] == "fin");
  assert(lines("", 19).empty() && lines("   ", 19).empty());
  // Exactly a line's width, then more.
  wrapped = lines("abcdefghij klm", 10);
  assert(wrapped.size() == 2 && wrapped[0] == "abcdefghij" && wrapped[1] == "klm");

  puts("morning: all tests passed");
  return 0;
}

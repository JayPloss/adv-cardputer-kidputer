// Host test of the clock's trust tiers (docs/morning-overview.md, part A).
//   g++ -std=c++11 -I firmware/src firmware/tools/clock-trust-test.cpp -o /tmp/clock-trust-test && /tmp/clock-trust-test
#include <cassert>
#include <cstdio>
#include "apps/time/clock_trust.h"

using namespace clocktrust;

static Stored make(int64_t synced, int64_t boot, int64_t mark) { Stored s; s.synced = synced; s.boot = boot; s.mark = mark; return s; }

int main() {
  const int64_t build = 1795000000;      // 2026-11-18
  const int64_t synced = 1795500000;     // 2026-11-24, a sync
  const int64_t hour = 3600;

  // Never synced, first boot: the build time, unknown, nothing rings, no rules.
  Boot first = onBoot(Stored{}, 0, true, build);
  assert(!first.state.chipKept && first.setTo == build);
  assert(tier(first.state, build) == Tier::Unknown && rules(first.state, build) == Rules::Off && !alarmsRing(first.state, build));

  // A restart (not power-on) with the chip's clock intact: kept, no change to the clock.
  Stored stored = make(synced, synced - 100, synced + 2 * hour);
  Boot soft = onBoot(stored, synced + 3 * hour, false, build);
  assert(soft.state.chipKept && soft.setTo == 0);
  assert(tier(soft.state, synced + 3 * hour) == Tier::Synced && rules(soft.state, synced + 3 * hour) == Rules::Exact);
  assert(tier(soft.state, synced + 7 * hour) == Tier::Estimated && alarmsRing(soft.state, synced + 7 * hour));
  int64_t ruleNow = 0;
  assert(rules(soft.state, synced + 7 * hour) == Rules::Grace && rulesTime(soft.state, synced + 7 * hour, ruleNow) && ruleNow == synced + 7 * hour - kGraceS);
  assert(rules(soft.state, synced + 73 * hour) == Rules::Off && !rulesTime(soft.state, synced + 73 * hour, ruleNow));
  assert(tier(soft.state, synced + 73 * hour) == Tier::Estimated && alarmsRing(soft.state, synced + 73 * hour));

  // A chip a minute behind the mark (rounding, a write just before the reset): moved up to the mark, still kept.
  Boot behind = onBoot(stored, stored.mark - 60, false, build);
  assert(behind.state.chipKept && behind.setTo == stored.mark);

  // Rolled back past the slack: lost. The clock starts at the mark and nothing rings by it.
  Boot rolled = onBoot(stored, stored.mark - 3 * hour, false, build);
  assert(!rolled.state.chipKept && rolled.setTo == stored.mark && tier(rolled.state, stored.mark) == Tier::Unknown);

  // Power pulled: whatever the chip reads, it is unknown, from the mark.
  Boot pulled = onBoot(stored, 5, true, build);
  assert(!pulled.state.chipKept && pulled.setTo == stored.mark);
  Boot pulledSane = onBoot(stored, stored.mark + hour, true, build);
  assert(!pulledSane.state.chipKept && pulledSane.setTo == stored.mark);

  // A chip reset that kept a time but the device never synced: unknown, but the later of chip and build.
  Boot unsynced = onBoot(make(0, 0, 0), build + hour, false, build);
  assert(!unsynced.state.chipKept && unsynced.setTo == build + hour);

  // The mark moves forward only on a kept clock; a sync moves it either way and makes the clock exact.
  State state = pulled.state;
  assert(!advanceMark(state, stored.mark + 600));
  onSync(state, stored.mark - 30);  // The server says the mark was a little fast.
  assert(state.chipKept && state.mark == stored.mark - 30 && tier(state, stored.mark) == Tier::Synced);
  assert(advanceMark(state, stored.mark + 600) && state.mark == stored.mark + 600);
  assert(!advanceMark(state, stored.mark));

  // A clock behind its own last sync isn't trusted for limits, though it still shows.
  assert(rules(state, state.synced - 10) == Rules::Off);

  puts("clock trust: all tests passed");
  return 0;
}

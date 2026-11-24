#pragma once

#include <stdint.h>

// How far the device can trust its own clock, with no hardware, so a host test can run it
// (firmware/tools/clock-trust-test.cpp). Design: docs/morning-overview.md, part A; thresholds from
// docs/PARENT-CONTROLS.md. Times are UTC seconds.
namespace clocktrust {

constexpr int64_t kSyncedForS = 6 * 3600;      // A time server answered this recently: exact.
constexpr int64_t kPlausibleForS = 72 * 3600;  // Kept by the chip since then: rules with grace. Older: rules off.
constexpr int64_t kGraceS = 10 * 60;           // Quiet-time windows start (and end) this late on a stale clock.
constexpr int64_t kRollbackSlackS = 2 * 60;    // A clock at most this far behind the mark is moved up to it.
constexpr int64_t kSaneEpoch = 1735689600;     // 2025-01-01: anything earlier is a chip that lost its time.

// Synced: shown plainly. Estimated: the chip kept time since a sync; shown with a marker. Unknown: the
// chip lost the time; a guess is shown in grey and nothing rings by it.
enum class Tier : uint8_t { Synced, Estimated, Unknown };
// How the parents' quiet-time rules apply: exactly, late by kGraceS, or not at all.
enum class Rules : uint8_t { Exact, Grace, Off };

struct Stored {
  int64_t synced = 0;  // Last time-server answer, 0 if never.
  int64_t boot = 0;    // The boot before this one.
  int64_t mark = 0;    // High-water mark: the latest trusted time.
};

struct State {
  int64_t synced = 0;
  int64_t mark = 0;
  bool chipKept = false;  // The chip's clock carries real time this boot (kept across the reset, or synced since).
};

struct Boot {
  State state;
  int64_t setTo = 0;  // What to set the clock to now; 0 to leave the chip's clock as it is.
};

// At boot, before anything sets the clock. chipNow is what the chip's clock reads; powerOn is true for a
// power-on or brownout reset (the RTC domain lost power, so its count is gone); buildEpoch is the firmware's
// build time, the floor for certificate checks.
inline Boot onBoot(const Stored &stored, int64_t chipNow, bool powerOn, int64_t buildEpoch) {
  Boot result;
  result.state.synced = stored.synced;
  result.state.mark = stored.mark;
  const bool sane = chipNow >= kSaneEpoch;
  const bool notBehind = stored.mark == 0 || chipNow >= stored.mark - kRollbackSlackS;
  if (!powerOn && sane && stored.synced > 0 && notBehind) {
    result.state.chipKept = true;
    if (chipNow < stored.mark) result.setTo = stored.mark;  // Within the slack: forward to the mark.
    return result;
  }
  // Lost: the last time known, or the build time if that is later. A guess, and only shown as one.
  result.state.chipKept = false;
  result.setTo = stored.mark > buildEpoch ? stored.mark : buildEpoch;
  if (sane && chipNow > result.setTo && !powerOn) result.setTo = chipNow;
  return result;
}

// A time server answered: the clock is exact from now and the mark follows it either way.
inline void onSync(State &state, int64_t serverNow) {
  state.synced = serverNow; state.mark = serverNow; state.chipKept = true;
}

// Every few minutes and before sleep: the mark only moves forward, and only on a clock worth trusting.
inline bool advanceMark(State &state, int64_t now) {
  if (!state.chipKept || now <= state.mark) return false;
  state.mark = now; return true;
}

inline Tier tier(const State &state, int64_t now) {
  if (!state.chipKept || state.synced <= 0) return Tier::Unknown;
  const int64_t age = now - state.synced;
  return age >= 0 && age < kSyncedForS ? Tier::Synced : Tier::Estimated;
}

inline Rules rules(const State &state, int64_t now) {
  if (!state.chipKept || state.synced <= 0) return Rules::Off;
  const int64_t age = now - state.synced;
  if (age < 0) return Rules::Off;  // A clock behind its own last sync: not trusted for limits.
  if (age < kSyncedForS) return Rules::Exact;
  return age <= kPlausibleForS ? Rules::Grace : Rules::Off;
}

// The time quiet-time rules read: late by the grace on a stale clock. False when no rule applies.
inline bool rulesTime(const State &state, int64_t now, int64_t &ruleNow) {
  switch (rules(state, now)) {
    case Rules::Exact: ruleNow = now; return true;
    case Rules::Grace: ruleNow = now - kGraceS; return true;
    default: return false;
  }
}

// Alarms ring by a clock that is synced or kept by the chip, never by a guess.
inline bool alarmsRing(const State &state, int64_t now) { return tier(state, now) != Tier::Unknown; }

}  // namespace clocktrust

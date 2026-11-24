#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

// Morning Overview v2 (docs/morning-overview.md, part B): when it opens, which brief it shows, and how its
// text breaks into lines. No hardware, so a host test runs it (firmware/tools/morning-test.cpp).
namespace morning {

// An alarm set from 04:00 to 10:59 is a morning alarm: the one the overview follows.
inline bool isMorningAlarm(uint8_t hour) { return hour >= 4 && hour < 11; }

// Once a day: the first snooze or stop of a morning alarm opens the overview and plays it (snooze is the
// better moment: it plays during the five minutes). A later stop that day opens it without playing again.
struct Trigger {
  int16_t day = -1;  // tm_yday it last opened on
  bool played = false;
};
struct Action {
  bool open = false;
  bool play = false;
};
inline Action onAlarmHandled(Trigger &trigger, int16_t day, uint8_t alarmHour) {
  Action action;
  if (!isMorningAlarm(alarmHour)) return action;
  action.open = true;
  if (trigger.day != day) { trigger.day = day; trigger.played = false; }
  if (!trigger.played) { action.play = true; trigger.played = true; }
  return action;
}

// Which cached brief to show: today's; else, with the date unknown or no brief for today, the newest one at
// or before today (the newest of all when the date is unknown). -1 when there are none. Dates are YYYY-MM-DD,
// so they compare as text.
inline int pickBrief(const std::vector<std::string> &dates, const char *today) {
  int best = -1;
  for (int index = 0; index < static_cast<int>(dates.size()); index++) {
    const std::string &date = dates[index];
    if (date.size() != 10) continue;
    if (today && *today) {
      if (date == today) return index;
      if (date > today) continue;  // tomorrow's, fetched early: not yet
    }
    if (best < 0 || date > dates[best]) best = index;
  }
  return best;
}

// A brief is kept three days.
inline bool expired(const char *date, const char *today, int keepDays = 3) {
  if (!date || !today || strlen(date) != 10 || strlen(today) != 10) return false;
  auto days = [](const char *text) {
    int year = 0, month = 0, day = 0;
    if (sscanf(text, "%d-%d-%d", &year, &month, &day) != 3) return 0L;
    // Days from a fixed point (civil calendar), enough to subtract two dates.
    year -= month <= 2;
    const long era = (year >= 0 ? year : year - 399) / 400;
    const long yoe = year - era * 400;
    const long doy = (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
    const long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe;
  };
  return days(today) - days(date) >= keepDays;
}

// UTF-8 aware line breaking for Font0: at most `width` characters a line, broken at spaces; a word longer
// than a line is cut. Returns [start, end) byte ranges into text.
struct Range {
  uint16_t start;
  uint16_t end;
};
inline bool continuation(unsigned char byte) { return (byte & 0xC0) == 0x80; }
inline std::vector<Range> wrap(const char *text, int width) {
  std::vector<Range> lines;
  if (!text || width <= 0) return lines;
  const size_t length = strlen(text);
  size_t start = 0;
  while (start < length) {
    while (start < length && text[start] == ' ') start++;  // no line starts with a space
    if (start >= length) break;
    if (text[start] == '\n') { lines.push_back({static_cast<uint16_t>(start), static_cast<uint16_t>(start)}); start++; continue; }
    size_t position = start, lastSpace = 0;
    int characters = 0;
    bool brokeAtNewline = false;
    while (position < length && characters < width) {
      if (text[position] == '\n') { brokeAtNewline = true; break; }
      if (text[position] == ' ') lastSpace = position;
      position++;
      while (position < length && continuation(static_cast<unsigned char>(text[position]))) position++;
      characters++;
    }
    size_t end = position;
    if (!brokeAtNewline && position < length && text[position] != ' ' && text[position] != '\n' && lastSpace > start) end = lastSpace;
    size_t trimmed = end;
    while (trimmed > start && text[trimmed - 1] == ' ') trimmed--;
    lines.push_back({static_cast<uint16_t>(start), static_cast<uint16_t>(trimmed)});
    start = end;
    if (start < length && text[start] == '\n') start++;
  }
  return lines;
}

}  // namespace morning

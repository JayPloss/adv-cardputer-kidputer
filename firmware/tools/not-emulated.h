#pragma once
// Device functions the runtime emulator (cardputer-emulator) cannot model, and the stand-ins compiled in their
// place. main.cpp includes this only when EMU_HEAP_START is defined, which only the emulator's build defines;
// never part of a device build. Every entry here is a layer the emulator
// does NOT cover: the gate prints this list on every run, and NOT_EMULATED_SCENARIOS in the gate names any
// scenario that depends on it. Keep the list short and specific; each entry says why it can't be emulated.
// (Jay, 2026-11-24: "you can override the gate where the functions can't be emulated".)

#include <stdint.h>

// 1. esp_reset_reason(): the cause of the last reset, read from the chip's RTC domain. The emulator starts a
//    fresh process each boot and its wall clock always equals world time (settimeofday is a no-op there), so
//    a chip clock that survived a restart, or was lost with the power, cannot exist in it. The stand-in says
//    "software restart" so the firmware keeps the emulator's clock, as a device does after a restart.
//    Not covered: the unknown tier after a power loss, and the high-water mark on a clock that went back.
#ifndef ESP_RST_POWERON
typedef enum {
  ESP_RST_UNKNOWN, ESP_RST_POWERON, ESP_RST_EXT, ESP_RST_SW, ESP_RST_PANIC, ESP_RST_INT_WDT, ESP_RST_TASK_WDT,
  ESP_RST_WDT, ESP_RST_DEEPSLEEP, ESP_RST_BROWNOUT, ESP_RST_SDIO
} esp_reset_reason_t;
inline esp_reset_reason_t esp_reset_reason() { return ESP_RST_SW; }
#endif

// 2. gettimeofday(): the chip's clock. The emulator keeps one (the world's time plus an offset that settimeofday
//    sets and light sleep's drift adds to, `clock-drift`), but only getLocalTime reads it there (libc's
//    gettimeofday is the PC's clock). The stand-in reads it through getLocalTime, to the second.
//    Not covered: sub-second readings, so the drift logged on a sync is whole seconds here.
#include <sys/time.h>
#include <time.h>
inline int emuChipClock(struct timeval *value) {
  struct tm local{};
  getLocalTime(&local, 0);
  value->tv_sec = mktime(&local);
  value->tv_usec = 0;
  return 0;
}
#define gettimeofday(value, zone) emuChipClock(value)

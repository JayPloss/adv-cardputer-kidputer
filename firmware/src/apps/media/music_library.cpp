#include "music_library.h"
#include "os.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <SD.h>
#include <WiFi.h>
#include <esp_random.h>
#include <mbedtls/md5.h>

#include <algorithm>
#include <cerrno>
#include <dirent.h>
#include <deque>
#include <new>

#if __has_include("music_secrets.h")
#include "music_secrets.h"
#endif
#ifndef SUPACHAT_MUSIC_HOST
#define SUPACHAT_MUSIC_HOST ""
#define SUPACHAT_MUSIC_USER ""
#define SUPACHAT_MUSIC_PASSWORD ""
#endif

namespace library {
namespace {
// The emulator points a build at the server through the tailnet with SUPACHAT_MUSIC_HOST_OVERRIDE.
#ifdef SUPACHAT_MUSIC_HOST_OVERRIDE
constexpr char kHost[] = SUPACHAT_MUSIC_HOST_OVERRIDE;
#else
constexpr char kHost[] = SUPACHAT_MUSIC_HOST;
#endif
constexpr char kUser[] = SUPACHAT_MUSIC_USER;
constexpr char kPassword[] = SUPACHAT_MUSIC_PASSWORD;
constexpr char kBooksFolderName[] = "Books";
// Low enough that the server re-encodes, which is also what makes timeOffset work.
constexpr int kMusicKbps = 96;
constexpr int kBooksKbps = 48;
constexpr uint32_t kTimeoutMs = 4000;
// Away from home the server's address goes nowhere, and every wait for it would show on the screen.
constexpr uint32_t kConnectTimeoutMs = 2000;
constexpr uint32_t kServerRetryMs = 60000;
constexpr size_t kCardTrackLimit = 300;

enum class JobKind : uint8_t { CardAlbums, CardTracks, Adjacent, SavePlace, ClearPlace, Albums, Liked, Search, Tracks, SaveBookmark, DeleteBookmark, Like, Unlike };
struct Job { JobKind kind; String text; int number; Collection collection; String extra; uint32_t generation; };

SemaphoreHandle_t mutex = nullptr;
TaskHandle_t taskHandle = nullptr;
volatile bool endRequested = false;
std::deque<Job> jobs;
std::vector<Item> finished;
bool itemsFresh = false;
volatile uint32_t wantedGeneration = 0;
uint32_t runningGeneration = 0;
Item adjacentItem;
bool adjacentReady = false;
bool adjacentFound = false;
volatile Status currentStatus = Status::Idle;
const char *volatile failureText = "";
// getMusicFolders answers once; -1 means not looked up yet, 0 means no such library.
int musicFolderId = -1;
int booksFolderId = -1;

String urlEncode(const String &value) {
  static const char hex[] = "0123456789ABCDEF";
  String out;
  out.reserve(value.length() + 8);
  for (size_t i = 0; i < value.length(); i++) {
    const uint8_t c = value[i];
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') out += static_cast<char>(c);
    else { out += '%'; out += hex[c >> 4]; out += hex[c & 15]; }
  }
  return out;
}

String apiUrl(const char *method) {
  char salt[13];
  snprintf(salt, sizeof(salt), "%08x%04x", static_cast<unsigned>(esp_random()), static_cast<unsigned>(esp_random() & 0xffff));
  const String seed = String(kPassword) + salt;
  uint8_t digest[16];
  mbedtls_md5_ret(reinterpret_cast<const uint8_t *>(seed.c_str()), seed.length(), digest);
  char token[33];
  for (int i = 0; i < 16; i++) sprintf(token + i * 2, "%02x", digest[i]);
  return String("http://") + kHost + "/rest/" + method + "?u=" + urlEncode(kUser) + "&t=" + token + "&s=" + salt +
         "&v=1.16.1&c=cardputer&f=json";
}

uint32_t serverDownAt = 0;
bool serverWasDown = false;
bool serverKnownDown() { return serverWasDown && millis() - serverDownAt < kServerRetryMs; }

// What ArduinoJson reads the socket through. Stream's own timed read spins without yielding,
// and on a slow server that starves core 0's idle task until the watchdog restarts the device.
struct YieldingReader {
  WiFiClient &client;
  os::Pacer pacer;  // A big reply from a fast server never waits below: os.h's rule.
  int read() {
    const uint32_t started = millis();
    while (millis() - started < kTimeoutMs) {
      const int value = client.read();
      if (value >= 0) { pacer.step(); return value; }
      if (!client.connected()) return -1;
      vTaskDelay(pdMS_TO_TICKS(2));
    }
    return -1;
  }
  size_t readBytes(char *buffer, size_t length) {
    size_t count = 0;
    while (count < length) {
      const int value = read();
      if (value < 0) break;
      buffer[count++] = static_cast<char>(value);
    }
    return count;
  }
};

// Fetches a Subsonic call with only the filtered fields kept in RAM. HTTP/1.0
// keeps the server from chunking, so the JSON parser can read the socket directly.
bool fetch(const String &url, JsonDocument &filter, JsonDocument &document) {
  if (!configured()) { failureText = "SERVER NOT SET UP"; return false; }
  if (WiFi.status() != WL_CONNECTED) { failureText = "NO WIFI"; return false; }
  HTTPClient http;
  http.useHTTP10(true);
  if (serverKnownDown()) { failureText = "NO SERVER"; return false; }
  http.setConnectTimeout(kConnectTimeoutMs);
  http.setTimeout(kTimeoutMs);
  http.begin(url);
  const int code = http.GET();
  if (code != HTTP_CODE_OK) Serial.printf("library: server answered %d\n", code);
  if (code < 0) { serverDownAt = millis(); serverWasDown = true; }
  if (code != HTTP_CODE_OK) { failureText = code < 0 ? "NO SERVER" : "SERVER ERROR"; http.end(); return false; }
  serverWasDown = false;
  filter["subsonic-response"]["status"] = true;
  YieldingReader reader{http.getStream()};
  const DeserializationError error = deserializeJson(document, reader, DeserializationOption::Filter(filter));
  http.end();
  if (error) { failureText = "BAD REPLY"; return false; }
  if (strcmp(document["subsonic-response"]["status"] | "", "ok") != 0) { failureText = "LOGIN REFUSED"; return false; }
  return true;
}

void call(const String &url) {
  JsonDocument filter, document;
  fetch(url, filter, document);
}

void lookUpFolders() {
  if (musicFolderId >= 0) return;
  JsonDocument filter, document;
  filter["subsonic-response"]["musicFolders"]["musicFolder"][0]["id"] = true;
  filter["subsonic-response"]["musicFolders"]["musicFolder"][0]["name"] = true;
  if (!fetch(apiUrl("getMusicFolders"), filter, document)) return;
  int music = 0, books = 0;
  for (JsonObjectConst folder : document["subsonic-response"]["musicFolders"]["musicFolder"].as<JsonArrayConst>()) {
    if (strcmp(folder["name"] | "", kBooksFolderName) == 0) books = folder["id"] | 0;
    else if (!music) music = folder["id"] | 0;
  }
  musicFolderId = music;
  booksFolderId = books;
}

void publish(std::vector<Item> &&items) {
  if (runningGeneration != wantedGeneration) return;
  xSemaphoreTake(mutex, portMAX_DELAY);
  finished = std::move(items);
  itemsFresh = true;
  xSemaphoreGive(mutex);
  currentStatus = Status::Ready;
}

void addAlbum(std::vector<Item> &items, JsonObjectConst album) {
  Item item;
  item.id = album["id"] | "";
  item.title = album["name"] | "?";
  item.detail = album["artist"] | "";
  item.seconds = album["duration"] | 0;
  items.push_back(std::move(item));
}

void addTrack(std::vector<Item> &items, JsonObjectConst track) {
  Item item;
  item.id = track["id"] | "";
  item.title = track["title"] | "?";
  item.detail = track["artist"] | "";
  item.seconds = track["duration"] | 0;
  item.liked = !track["starred"].isNull();
  items.push_back(std::move(item));
}

const char *cardRoot(Collection collection) { return collection == Collection::Books ? "/books" : "/music"; }

bool isMp3(const char *name) {
  const size_t length = strlen(name);
  return name[0] != '.' && length > 4 && (!strcmp(name + length - 4, ".mp3") || !strcmp(name + length - 4, ".MP3"));
}

// Reading a card folder. The core's openNextFile opens every entry it passes (a stat, then an opendir or a
// file with a 4 KB buffer), and when one of those allocations is refused (this app's budget, or the floor
// while Wi-Fi scans away from home) it returns an empty File, which ends the loop as if the folder had
// ended: a short list, no error. So the folder is read with readdir, which allocates nothing per entry
// (FAT's long names are on the stack: CONFIG_FATFS_LFN_STACK), each name's String is checked, and a list
// counts only when two passes in a row agree.
// Strings are only ever assigned from char pointers here: the core's String::move copies a byte out of a
// String whose allocation failed, from address 0, and the task crashes (WString.cpp, move()).
constexpr int kListPasses = 6;
constexpr uint32_t kListRetryMs = 300;
constexpr char kCardMount[] = "/sd";  // where SD.begin mounts the card

struct Pass {
  bool read = true;  // false: the card or the RAM failed part way
  size_t count = 0;
  uint32_t hash = 2166136261u;
};

// Sets `text` to `first` (its first `length` bytes), plus "/" and `second` when given. A refused
// allocation is tried again a few times: a dip in the heap passes in a moment.
bool assign(String &text, const char *first, size_t length, const char *second = nullptr) {
  const size_t want = length + (second ? 1 + strlen(second) : 0);
  for (int attempt = 0; attempt < 5; attempt++) {
    text = "";
    if (text.reserve(want) && text.concat(first, length) && (!second || (text.concat('/') && text.concat(second))) && text.length() == want) return true;
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  return false;
}

// One pass over `path`, keeping folders (or MP3s). With `items`, it also builds the list.
Pass readFolder(const String &path, bool folders, size_t limit, std::vector<Item> *items) {
  Pass pass;
  char where[320];
  snprintf(where, sizeof where, "%s%s", kCardMount, path.c_str());
  DIR *dir = opendir(where);
  if (!dir) { pass.read = !SD.exists(path); return pass; }  // There but not opened: no room for it.
  for (;;) {
    errno = 0;
    const dirent *entry = readdir(dir);
    if (!entry) { if (errno) pass.read = false; break; }
    const char *name = entry->d_name;
    const bool isDirectory = entry->d_type == DT_DIR;
    if (!isDirectory && entry->d_type != DT_REG) continue;
    if (folders ? !isDirectory : (isDirectory || !isMp3(name))) continue;
    if (folders && name[0] == '.') continue;
    if (pass.count >= limit) break;
    pass.count++;
    for (const char *c = name; *c; c++) pass.hash = (pass.hash ^ static_cast<uint8_t>(*c)) * 16777619u;
    pass.hash = (pass.hash ^ '/') * 16777619u;
    if (!items) continue;
    const size_t titleLength = strlen(name) - (folders ? 0 : 4);
    Item item;
    item.onCard = !folders;
    if (!assign(item.title, name, titleLength) || (folders && !assign(item.path, path.c_str(), path.length(), name)) || items->size() >= items->capacity()) {
      // This pass's list is lost, but it reads on: its count sizes the next one.
      pass.read = false;
      items = nullptr;
      continue;
    }
    items->push_back(std::move(item));
  }
  closedir(dir);
  return pass;
}

// The whole list: counted, then built, until two passes agree. False if they never did.
bool readFolderList(const String &path, bool folders, size_t limit, std::vector<Item> &items) {
  const size_t first = items.size();
  Pass last = readFolder(path, folders, limit, nullptr);
  for (int attempt = 0; attempt < kListPasses; attempt++) {
    items.resize(first);
    try {
      items.reserve(first + last.count + 1);
    } catch (const std::bad_alloc &) {
      vTaskDelay(pdMS_TO_TICKS(kListRetryMs));
      continue;
    }
    const Pass pass = readFolder(path, folders, limit, &items);
    if (pass.read && last.read && pass.count == last.count && pass.hash == last.hash) {
      std::sort(items.begin() + first, items.end(), [](const Item &a, const Item &b) { return a.title < b.title; });
      return true;
    }
    last = pass;
    vTaskDelay(pdMS_TO_TICKS(kListRetryMs));
  }
  return false;
}

constexpr const char *kCardNotRead = "CARD NOT READ: LOW RAM";

// False when the card's list couldn't be read whole; nothing is added then, rather than part of it.
bool addCardFolders(std::vector<Item> &items, Collection collection) {
  const size_t first = items.size();
  if (readFolderList(cardRoot(collection), true, SIZE_MAX, items)) return true;
  items.resize(first);
  return false;
}

String placePath(const String &folder) { return folder + "/.place"; }

// A place file ends in "ok", so one cut off by a power loss reads as no place, not a wrong one.
bool readPlace(const String &path, String &trackPath, uint32_t &seconds) {
  File place = SD.open(path, FILE_READ);
  if (!place) return false;
  trackPath = place.readStringUntil('\n');
  seconds = place.readStringUntil('\n').toInt();
  const String end = place.readStringUntil('\n');
  place.close();
  return end == "ok";
}

bool runCardTracks(const Job &job) {
  std::vector<Item> items;
  if (!readFolderList(job.text, false, kCardTrackLimit, items)) {
    failureText = SD.exists(job.text) ? kCardNotRead : "NOT ON CARD";
    return false;
  }
  String trackPath;
  uint32_t seconds = 0;
  if (readPlace(placePath(job.text), trackPath, seconds) || readPlace(placePath(job.text) + ".new", trackPath, seconds)) {
    // A 1 ms floor still marks a saved place at the very start of a chapter.
    for (Item &item : items) if (job.text + "/" + item.title + ".mp3" == trackPath) item.resumeMs = std::max<uint32_t>(1, seconds * 1000);
  }
  publish(std::move(items));
  return true;
}

// A random song of the folder other than the one playing, for shuffle: counted on one pass, then the
// chosen one found on a second, so the folder is never listed into RAM.
void runRandom(const Job &job) {
  char where[320];
  snprintf(where, sizeof where, "%s%s", kCardMount, job.text.c_str());
  auto eligible = [&](const dirent *entry, String &title) {
    if (entry->d_type != DT_REG || !isMp3(entry->d_name)) return false;
    title = entry->d_name;
    if (title.length() != strlen(entry->d_name)) return false;
    title.remove(title.length() - 4);
    return title != job.extra;
  };
  int count = 0;
  String title, chosen;
  if (DIR *dir = opendir(where)) {
    while (const dirent *entry = readdir(dir)) if (eligible(entry, title)) count++;
    closedir(dir);
  }
  if (count > 0) {
    int pick = static_cast<int>(esp_random() % static_cast<uint32_t>(count));
    if (DIR *dir = opendir(where)) {
      while (const dirent *entry = readdir(dir)) if (eligible(entry, title) && pick-- == 0) { chosen = title.c_str(); break; }
      closedir(dir);
    }
  }
  xSemaphoreTake(mutex, portMAX_DELAY);
  adjacentItem = Item();
  adjacentItem.onCard = true;
  adjacentItem.title = chosen.c_str();
  adjacentFound = chosen.length() > 0;
  adjacentReady = true;
  xSemaphoreGive(mutex);
}

void runAdjacent(const Job &job) {
  if (job.number == kRandomTrack) { runRandom(job); return; }
  String best;
  const bool forward = job.number > 0;
  char where[320];
  snprintf(where, sizeof where, "%s%s", kCardMount, job.text.c_str());
  if (DIR *dir = opendir(where)) {
    while (const dirent *entry = readdir(dir)) {
      if (entry->d_type != DT_REG || !isMp3(entry->d_name)) continue;
      String title = entry->d_name;
      if (title.length() != strlen(entry->d_name)) continue;
      title.remove(title.length() - 4);
      const bool past = forward ? title > job.extra : title < job.extra;
      if (past && (best.isEmpty() || (forward ? title < best : title > best))) best = title.c_str();
    }
    closedir(dir);
  }
  xSemaphoreTake(mutex, portMAX_DELAY);
  adjacentItem = Item();
  adjacentItem.onCard = true;
  adjacentItem.title = best.c_str();
  adjacentFound = best.length() > 0;
  adjacentReady = true;
  xSemaphoreGive(mutex);
}

void writeCardPlace(const Job &job) {
  // Written whole under a new name first: a power loss at any moment leaves one good copy.
  const String path = placePath(job.text);
  const String fresh = path + ".new";
  // A cut between an earlier save's remove and rename left the only good copy under the new name.
  if (!SD.exists(path) && SD.exists(fresh)) {
    String ignoredPath;
    uint32_t ignoredSeconds = 0;
    if (readPlace(fresh, ignoredPath, ignoredSeconds)) SD.rename(fresh, path);
  }
  File place = SD.open(fresh, FILE_WRITE);
  if (!place) return;
  size_t wrote = place.print(job.extra);
  wrote += place.print('\n'); wrote += place.print(job.number); wrote += place.print("\nok\n");
  const bool complete = wrote >= job.extra.length() + 5;
  place.close();
  if (!complete) { SD.remove(fresh); return; }  // The card is full: keep the old place.
  SD.remove(path);
  SD.rename(fresh, path);
}

bool runAlbums(const Job &job) {
  std::vector<Item> items;
  const bool cardRead = job.number != 0 || addCardFolders(items, job.collection);
  const bool hasCardItems = !items.empty();
  const char *const nothing = !cardRead ? kCardNotRead : job.collection == Collection::Books ? "NO BOOKS ON CARD, NO SERVER" : "NO MUSIC ON CARD, NO SERVER";
  if (!configured() || WiFi.status() != WL_CONNECTED || serverKnownDown()) {
    failureText = nothing;
    if (hasCardItems) publish(std::move(items));
    return hasCardItems;
  }
  // The card shows at once. The server's albums join the list if and when they arrive. The copy is
  // checked: a String that couldn't be copied comes out empty, and a list with a blank book isn't shown.
  if (hasCardItems) {
    std::vector<Item> first;
    bool whole = true;
    try { first = items; } catch (const std::bad_alloc &) { whole = false; }
    for (size_t i = 0; whole && i < items.size(); i++) whole = first[i].title.length() == items[i].title.length() && first[i].path.length() == items[i].path.length();
    if (whole) publish(std::move(first));
  }
  lookUpFolders();
  const int folder = job.collection == Collection::Books ? booksFolderId : musicFolderId;
  if (musicFolderId < 0 || (job.collection == Collection::Books && folder == 0)) {
    failureText = nothing;
    return hasCardItems;
  }
  JsonDocument filter, document;
  JsonObject album = filter["subsonic-response"]["albumList2"]["album"][0].to<JsonObject>();
  album["id"] = true; album["name"] = true; album["artist"] = true; album["duration"] = true;
  String url = apiUrl("getAlbumList2") + "&type=alphabeticalByName&size=" + kPageSize + "&offset=" + job.number;
  if (folder) url += String("&musicFolderId=") + folder;
  if (!fetch(url, filter, document)) {
    failureText = nothing;
    return hasCardItems;
  }
  for (JsonObjectConst value : document["subsonic-response"]["albumList2"]["album"].as<JsonArrayConst>()) addAlbum(items, value);
  publish(std::move(items));
  return true;
}

bool runLiked() {
  JsonDocument filter, document;
  JsonObject song = filter["subsonic-response"]["starred2"]["song"][0].to<JsonObject>();
  song["id"] = true; song["title"] = true; song["artist"] = true; song["duration"] = true; song["starred"] = true;
  if (!fetch(apiUrl("getStarred2"), filter, document)) return false;
  std::vector<Item> items;
  for (JsonObjectConst value : document["subsonic-response"]["starred2"]["song"].as<JsonArrayConst>()) addTrack(items, value);
  publish(std::move(items));
  return true;
}

bool runSearch(const Job &job) {
  JsonDocument filter, document;
  JsonObject song = filter["subsonic-response"]["searchResult3"]["song"][0].to<JsonObject>();
  song["id"] = true; song["title"] = true; song["artist"] = true; song["duration"] = true; song["starred"] = true;
  String url = apiUrl("search3") + "&query=" + urlEncode(job.text) + "&artistCount=0&albumCount=0&songCount=" + kPageSize + "&songOffset=" + job.number;
  if (!fetch(url, filter, document)) return false;
  std::vector<Item> items;
  for (JsonObjectConst value : document["subsonic-response"]["searchResult3"]["song"].as<JsonArrayConst>()) addTrack(items, value);
  publish(std::move(items));
  return true;
}

bool runTracks(const Job &job) {
  JsonDocument filter, document;
  JsonObject song = filter["subsonic-response"]["album"]["song"][0].to<JsonObject>();
  song["id"] = true; song["title"] = true; song["artist"] = true; song["duration"] = true; song["starred"] = true;
  if (!fetch(apiUrl("getAlbum") + "&id=" + urlEncode(job.text), filter, document)) return false;
  std::vector<Item> items;
  for (JsonObjectConst value : document["subsonic-response"]["album"]["song"].as<JsonArrayConst>()) addTrack(items, value);

  JsonDocument marksFilter, marks;
  JsonObject mark = marksFilter["subsonic-response"]["bookmarks"]["bookmark"][0].to<JsonObject>();
  mark["position"] = true; mark["entry"]["id"] = true;
  if (fetch(apiUrl("getBookmarks"), marksFilter, marks)) {
    for (JsonObjectConst value : marks["subsonic-response"]["bookmarks"]["bookmark"].as<JsonArrayConst>()) {
      const char *trackId = value["entry"]["id"] | "";
      for (Item &item : items) if (item.id == trackId) item.resumeMs = value["position"] | 0;
    }
  }
  publish(std::move(items));
  return true;
}

void workerTask(void *) {
  os::bindTask(os::Owner::Music);
  for (;;) {
    if (endRequested) { endRequested = false; taskHandle = nullptr; vTaskDelete(nullptr); }
    xSemaphoreTake(mutex, portMAX_DELAY);
    const bool empty = jobs.empty();
    Job job{};
    if (!empty) { job = jobs.front(); jobs.pop_front(); }
    xSemaphoreGive(mutex);
    if (empty) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }
    bool ok = true;
    runningGeneration = job.generation;
    // Running out of RAM here shows on the screen instead of restarting the device.
    try {
    switch (job.kind) {
      case JobKind::CardAlbums: {
        std::vector<Item> items;
        if (addCardFolders(items, job.collection)) publish(std::move(items));
        else { failureText = kCardNotRead; ok = false; }
        break;
      }
      case JobKind::CardTracks: ok = runCardTracks(job); break;
      case JobKind::Adjacent: runAdjacent(job); break;
      case JobKind::SavePlace: writeCardPlace(job); break;
      case JobKind::ClearPlace: SD.remove(placePath(job.text)); SD.remove(placePath(job.text) + ".new"); break;
      case JobKind::Albums: ok = runAlbums(job); break;
      case JobKind::Liked: ok = runLiked(); break;
      case JobKind::Search: ok = runSearch(job); break;
      case JobKind::Tracks: ok = runTracks(job); break;
      case JobKind::SaveBookmark: call(apiUrl("createBookmark") + "&id=" + urlEncode(job.text) + "&position=" + job.number); break;
      case JobKind::DeleteBookmark: call(apiUrl("deleteBookmark") + "&id=" + urlEncode(job.text)); break;
      case JobKind::Like: call(apiUrl("star") + "&id=" + urlEncode(job.text)); break;
      case JobKind::Unlike: call(apiUrl("unstar") + "&id=" + urlEncode(job.text)); break;
    }
    } catch (const std::bad_alloc &) {
      failureText = "OUT OF MEMORY";
      os::recordFault(os::mediaOwner(), "list out of memory");
      ok = false;
    }
    if (!ok && runningGeneration == wantedGeneration) currentStatus = Status::Failed;
  }
}

void enqueue(Job job, bool loadsList) {
  if (!mutex) mutex = xSemaphoreCreateMutex();
  if (loadsList) { wantedGeneration = wantedGeneration + 1; currentStatus = Status::Loading; }
  job.generation = wantedGeneration;
  xSemaphoreTake(mutex, portMAX_DELAY);
  jobs.push_back(std::move(job));
  xSemaphoreGive(mutex);
}
}  // namespace

// ---- Card sync ----
namespace {
constexpr uint32_t kSyncReadMs = 15000;  // A download that stalls this long is dropped and tried next pass.
constexpr uint64_t kSyncSpareBytes = 8ull * 1024 * 1024;  // Left free on the card.
// A pass holds the one network connection the RAM allows, chat's included, so it takes a few songs
// and gives chat its turn; the next pass comes two minutes later.
constexpr int kSyncSongsPerPass = 4;
TaskHandle_t syncHandle = nullptr;
volatile bool syncStop = false;
volatile int syncAdded = 0;
String syncAddedNames;  // Written by the sync task, read once it has ended.
int syncNamesListed = 0;
volatile int syncTotal = -1, syncDone = 0, syncMissing = 0;
const char *volatile syncState = "NOT RUN YET";

// card.py's safe(): \/:*?"<>| become _, then dots and spaces come off both ends.
void safeName(const char *in, String &out) {
  out = "";
  for (const char *c = in; *c; c++) out += strchr("\\/:*?\"<>|", *c) ? '_' : *c;
  int start = 0, end = out.length();
  while (start < end && (out[start] == '.' || out[start] == ' ')) start++;
  while (end > start && (out[end - 1] == '.' || out[end - 1] == ' ')) end--;
  out = out.substring(start, end);
}

bool onCard(const String &path) { return SD.exists(path); }

// A song already on the card under another number (the playlist was reordered): its name after "NNN ".
bool renameRenumbered(const String &folder, const String &name) {
  DIR *dir = opendir((String(kCardMount) + folder).c_str());
  if (!dir) return false;
  const char *tail = name.c_str() + 4;
  String found;
  for (const dirent *entry; !syncStop && (entry = readdir(dir));) {
    const char *other = entry->d_name;
    if (strlen(other) > 4 && isdigit(other[0]) && isdigit(other[1]) && isdigit(other[2]) && other[3] == ' ' && !strcmp(other + 4, tail)) { found = other; break; }
  }
  closedir(dir);
  if (!found.length() || !SD.rename(folder + "/" + found, folder + "/" + name)) return false;
  for (const char *kind : {".lrc", ".nolrc"}) {  // Its lyrics follow it.
    const String from = folder + "/" + found.substring(0, found.length() - 4) + kind;
    if (SD.exists(from)) SD.rename(from, folder + "/" + name.substring(0, name.length() - 4) + kind);
  }
  return true;
}

bool numbered(const char *name) {
  return strlen(name) > 8 && isdigit(name[0]) && isdigit(name[1]) && isdigit(name[2]) && name[3] == ' ' && isMp3(name);
}

bool inManifest(const String &manifest, const char *name) {
  File list = SD.open(manifest, FILE_READ);
  bool found = false;
  while (list && list.available() && !found) {
    const String line = list.readStringUntil('\n');
    const int tab = line.indexOf('\t');
    found = tab > 0 && !strcmp(line.c_str() + tab + 1, name);
  }
  if (list) list.close();
  return found;
}

// Songs taken off the playlist leave the kid's folder: moved, not deleted, to /music/.removed/<Playlist>
// (hidden from the player), so one put back on the list comes back without a download. Only numbered
// songs ("001 ..."), the sync's own names, are moved; anything else in the folder stays.
int moveRemoved(const String &folder, const String &manifest, int total) {
  int songs = 0;
  if (DIR *dir = opendir((String(kCardMount) + folder).c_str())) {
    for (const dirent *entry; (entry = readdir(dir));) songs += numbered(entry->d_name);
    closedir(dir);
  }
  if (songs <= total) return 0;  // Nothing extra: no need to read the list against every file.
  const String bin = "/music/.removed/" + folder.substring(folder.lastIndexOf('/') + 1);
  if (!SD.exists("/music/.removed")) SD.mkdir("/music/.removed");
  if (!SD.exists(bin)) SD.mkdir(bin);
  int moved = 0;
  for (bool again = true; again && !syncStop;) {
    again = false;
    String stray;
    if (DIR *dir = opendir((String(kCardMount) + folder).c_str())) {
      for (const dirent *entry; !syncStop && (entry = readdir(dir));)
        if (numbered(entry->d_name) && !inManifest(manifest, entry->d_name)) { stray = entry->d_name; break; }
      closedir(dir);
    }
    if (stray.length()) {
      SD.remove(bin + "/" + stray);
      if (SD.rename(folder + "/" + stray, bin + "/" + stray)) {
        moved++; again = true;
        const String stem = stray.substring(0, stray.length() - 4);
        for (const char *kind : {".lrc", ".nolrc"}) { SD.remove(bin + "/" + stem + kind); SD.rename(folder + "/" + stem + kind, bin + "/" + stem + kind); }
      }
    }
  }
  return moved;
}

// Reads up to `marker` in the stream; false if it never came.
bool skipTo(YieldingReader &reader, const char *marker) {
  const size_t length = strlen(marker);
  size_t matched = 0;
  for (int c; (c = reader.read()) >= 0;) {
    matched = c == marker[matched] ? matched + 1 : (c == marker[0] ? 1 : 0);
    if (matched == length) return true;
  }
  return false;
}

// The playlist, one entry at a time from the socket into a list file on the card: 110 songs
// of Navidrome's JSON don't fit in RAM, one does. Lines: id TAB name.
int writeManifest(const String &playlistId, const String &folder, const String &manifest) {
  HTTPClient http;
  http.useHTTP10(true);
  http.setConnectTimeout(kConnectTimeoutMs);
  http.setTimeout(kTimeoutMs);
  http.begin(apiUrl("getPlaylist") + "&id=" + urlEncode(playlistId));
  if (http.GET() != HTTP_CODE_OK) { http.end(); syncState = "NO SERVER"; return -1; }
  YieldingReader reader{http.getStream()};
  File out = SD.open(manifest, FILE_WRITE);
  if (!out) { http.end(); syncState = "CARD NOT WRITABLE"; return -1; }
  int count = 0;
  if (skipTo(reader, "\"entry\":[")) {
    JsonDocument filter;
    filter["id"] = true; filter["title"] = true; filter["artist"] = true; filter["suffix"] = true;
    for (;;) {
      int c;
      do c = reader.read(); while (c == ',' || c == ' ' || c == '\n' || c == '\r');
      if (c != '{' || syncStop) break;
      // The parser wants the whole object: this one's brace was read, so it goes back in front.
      struct Prefixed { YieldingReader &inner; bool sent; explicit Prefixed(YieldingReader &reader) : inner(reader), sent(false) {} int read() { if (!sent) { sent = true; return '{'; } return inner.read(); }
        size_t readBytes(char *buffer, size_t length) { size_t n = 0; while (n < length) { const int v = read(); if (v < 0) break; buffer[n++] = v; } return n; } } prefixed(reader);
      JsonDocument entry;
      if (deserializeJson(entry, prefixed, DeserializationOption::Filter(filter))) break;
      count++;
      if (strcmp(entry["suffix"] | "", "mp3") != 0) continue;  // The player plays MP3s only.
      String title, artist;
      safeName(entry["title"] | "?", title);
      String first = entry["artist"] | "?";
      const int comma = first.indexOf(", ");
      if (comma > 0) first = first.substring(0, comma);
      safeName(first.c_str(), artist);
      char number[8];
      snprintf(number, sizeof number, "%03d ", count);
      out.printf("%s\t%s%s - %s.mp3\n", entry["id"] | "", number, title.c_str(), artist.c_str());
    }
  }
  out.close();
  http.end();
  return syncStop ? -1 : count;
}

// ---- Lyrics for the synced songs ----
// Each song's timed lyrics from Navidrome (getLyricsBySongId, its sidecar .lrc from wolfpack-sync's
// timing pass) beside it on the card as "<song>.lrc"; a song with no timed lyrics gets an empty
// "<song>.nolrc" so it isn't asked again. Once a day every song is asked again, since the timing
// pass replaces LRCLIB's times with ones measured on our recording.
constexpr int kLyricsPerPass = 40;
String songStem(const String &name) { return name.substring(0, name.length() - 4); }

bool fetchLyrics(const String &id, const String &folder, const String &name) {
  JsonDocument filter, document;
  filter["subsonic-response"]["lyricsList"]["structuredLyrics"][0]["synced"] = true;
  filter["subsonic-response"]["lyricsList"]["structuredLyrics"][0]["line"][0]["start"] = true;
  filter["subsonic-response"]["lyricsList"]["structuredLyrics"][0]["line"][0]["value"] = true;
  if (!fetch(apiUrl("getLyricsBySongId") + "&id=" + urlEncode(id), filter, document)) return false;
  const String stem = folder + "/" + songStem(name);
  JsonArrayConst timed;
  for (JsonObjectConst lyrics : document["subsonic-response"]["lyricsList"]["structuredLyrics"].as<JsonArrayConst>())
    if (lyrics["synced"] | false) { timed = lyrics["line"].as<JsonArrayConst>(); break; }
  if (timed.isNull() || timed.size() == 0) {
    SD.remove(stem + ".lrc");
    if (!SD.exists(stem + ".nolrc")) { File none = SD.open(stem + ".nolrc", FILE_WRITE); if (none) none.close(); }
    return true;
  }
  File out = SD.open(stem + ".lrc.new", FILE_WRITE);
  if (!out) return false;
  for (JsonObjectConst line : timed) {
    const uint32_t ms = line["start"] | 0;
    out.printf("[%02lu:%02lu.%02lu]%s\n", static_cast<unsigned long>(ms / 60000), static_cast<unsigned long>(ms / 1000 % 60),
               static_cast<unsigned long>(ms / 10 % 100), line["value"] | "");
  }
  out.close();
  SD.remove(stem + ".nolrc");
  SD.remove(stem + ".lrc");
  return SD.rename(stem + ".lrc.new", stem + ".lrc");
}

void syncLyrics(const String &folder, const String &manifest) {
  // Which day the last full ask was: the clock is seeded from the build, so a day is only trusted once
  // the clock looks like it was set (after the build's own day).
  const time_t now = time(nullptr);
  const long today = now > 1790000000 ? static_cast<long>(now / 86400) : -1;
  long asked = -2;
  if (File day = SD.open(folder + "/.lyrics-day", FILE_READ)) { asked = day.readStringUntil('\n').toInt(); day.close(); }
  const bool everySong = today >= 0 && asked != today;
  int fetched = 0;
  size_t offset = 0;
  bool complete = true;
  while (!syncStop) {
    File list = SD.open(manifest, FILE_READ);
    if (!list) break;
    list.seek(offset);
    if (!list.available()) { list.close(); break; }
    const String line = list.readStringUntil('\n');
    offset = list.position();
    list.close();
    const int tab = line.indexOf('\t');
    if (tab <= 0) continue;
    const String id = line.substring(0, tab), name = line.substring(tab + 1);
    const String stem = folder + "/" + songStem(name);
    if (!SD.exists(folder + "/" + name)) continue;
    if (!everySong && (SD.exists(stem + ".lrc") || SD.exists(stem + ".nolrc"))) continue;
    if (fetched >= kLyricsPerPass) { complete = false; break; }
    if (!fetchLyrics(id, folder, name)) { complete = false; break; }
    fetched++;
  }
  if (everySong && complete && !syncStop) {
    if (File day = SD.open(folder + "/.lyrics-day", FILE_WRITE)) { day.println(today); day.close(); }
  }
}

// One song, into a hidden file first, renamed only once every byte is there.
bool download(const String &id, const String &folder, const String &name) {
  const String part = folder + "/.part.mp3";
  HTTPClient http;
  http.useHTTP10(true);
  http.setConnectTimeout(kConnectTimeoutMs);
  http.setTimeout(kTimeoutMs);
  http.begin(apiUrl("download") + "&id=" + urlEncode(id));
  if (http.GET() != HTTP_CODE_OK) { http.end(); syncState = "NO SERVER"; return false; }
  const int length = http.getSize();
  if (length > 0 && SD.totalBytes() - SD.usedBytes() < static_cast<uint64_t>(length) + kSyncSpareBytes) { http.end(); syncState = "CARD FULL"; return false; }
  File out = SD.open(part, FILE_WRITE);
  if (!out) { http.end(); syncState = "CARD NOT WRITABLE"; return false; }
  WiFiClient &client = http.getStream();
  uint8_t buffer[1536];  // On the task's stack: no RAM held between passes.
  int written = 0;
  uint32_t lastData = millis();
  os::Pacer pacer;
  bool ok = true;
  while (!syncStop && (length < 0 || written < length)) {
    const int available = client.available();
    if (available <= 0) {
      if (!client.connected() && length < 0) break;
      if (!client.connected() || millis() - lastData > kSyncReadMs) { ok = false; break; }
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    const int got = client.read(buffer, std::min<int>(available, sizeof buffer));
    if (got <= 0) continue;
    if (out.write(buffer, got) != static_cast<size_t>(got)) { ok = false; syncState = "CARD FULL"; break; }
    written += got; lastData = millis();
    pacer.step();  // os.h: with data always waiting, this loop starved core 0 and tripped the watchdog.
  }
  out.close();
  http.end();
  ok = ok && !syncStop && (length < 0 || written == length) && written > 0;
  if (!ok) { SD.remove(part); return false; }
  SD.remove(folder + "/" + name);
  return SD.rename(part, folder + "/" + name);
}

// The shared alarm sounds: a public playlist of this name (tools/sd-cards/ringtones.py), mirrored into
// /music/Sonneries beside the owner's songs, where the alarm's sound picker shows it first.
constexpr char kRingtonesPlaylist[] = "Sonneries";

// One playlist into /music/<name>: the manifest, renames, downloads (shared cap with the pass), removed
// songs moved aside, then the owner's lyrics. Leaves syncState as UP TO DATE, MORE TO GET, PAUSED or a failure.
void syncPlaylist(const String &playlistId, const String &playlistName, bool ringtones) {
  syncMissing = 0; syncDone = 0;
  // Ringtones are a few seconds each (about 110 KB): all of them in one pass.
  const int cap = ringtones ? 40 : kSyncSongsPerPass;
  String folder;
  safeName(playlistName.c_str(), folder);
  folder = "/music/" + folder;
  if (!SD.exists("/music")) SD.mkdir("/music");
  if (!SD.exists(folder)) SD.mkdir(folder);
  const String manifest = folder + "/.sync.txt";
  const int total = writeManifest(playlistId, folder, manifest);
  if (total < 0) return;
  if (!ringtones) syncTotal = total;
  // Count what's missing first, so STATUS can say how many are left.
  int missing = 0;
  {
    File list = SD.open(manifest, FILE_READ);
    while (list && list.available() && !syncStop) {
      String line = list.readStringUntil('\n');
      const int tab = line.indexOf('\t');
      if (tab > 0 && !onCard(folder + "/" + line.substring(tab + 1))) missing++;
    }
    if (list) list.close();
  }
  syncMissing = missing;
  if (!missing) {
    moveRemoved(folder, manifest, total);
    if (!ringtones) { syncState = "LYRICS"; syncLyrics(folder, manifest); }
    if (!syncStop) syncState = "UP TO DATE";
    return;
  }
  syncState = "GETTING";
  File list = SD.open(manifest, FILE_READ);
  size_t offset = 0;
  bool failed = false;
  while (list && !syncStop && !failed) {
    list.seek(offset);
    if (!list.available()) break;
    String line = list.readStringUntil('\n');
    offset = list.position();
    const int tab = line.indexOf('\t');
    if (tab <= 0) continue;
    const String id = line.substring(0, tab), name = line.substring(tab + 1);
    if (onCard(folder + "/" + name)) continue;
    if (syncDone >= cap) { syncState = "MORE TO GET"; break; }
    // The card file stays closed while a song downloads: two open files is RAM the player may want.
    list.close();
    if (renameRenumbered(folder, name)) { syncDone = syncDone + 1; }
    else {
      const bool got = download(id, folder, name);
      if (got) {
        syncDone = syncDone + 1;
        if (!ringtones) syncAdded = syncAdded + 1;
        if (!ringtones && syncNamesListed < 6) { if (syncAddedNames.length()) syncAddedNames += ", "; syncAddedNames += name.substring(4, name.length() - 4); syncNamesListed++; }
      }
      else failed = true;
    }
    list = SD.open(manifest, FILE_READ);
  }
  if (list) list.close();
  if (syncStop) syncState = "PAUSED";
  else if (!failed && strcmp(syncState, "MORE TO GET")) {
    moveRemoved(folder, manifest, total);
    if (!ringtones) { syncState = "LYRICS"; syncLyrics(folder, manifest); }
    syncState = syncStop ? "PAUSED" : "UP TO DATE";
  }
}

void syncPass() {
  syncDone = 0; syncMissing = 0;
  if (!configured()) { syncState = "SERVER NOT SET UP"; return; }
  if (WiFi.status() != WL_CONNECTED) { syncState = "NO WIFI"; return; }
  if (!SD.exists("/")) { syncState = "NO CARD"; return; }
  syncState = "LOOKING";
  JsonDocument filter, document;
  filter["subsonic-response"]["playlists"]["playlist"][0]["id"] = true;
  filter["subsonic-response"]["playlists"]["playlist"][0]["name"] = true;
  if (!fetch(apiUrl("getPlaylists"), filter, document)) { syncState = failureText; return; }
  String playlistId, playlistName, ringtonesId;
  for (JsonObjectConst list : document["subsonic-response"]["playlists"]["playlist"].as<JsonArrayConst>()) {
    const String name = list["name"] | "";
    if (name.equalsIgnoreCase(kUser)) { playlistId = list["id"] | ""; playlistName = name; }
    else if (name == kRingtonesPlaylist) ringtonesId = list["id"] | "";
  }
  document.clear();
  if (playlistId.length()) syncPlaylist(playlistId, playlistName, false);
  else syncState = "NO PLAYLIST";
  const bool ownDone = !strcmp(syncState, "UP TO DATE") || !strcmp(syncState, "NO PLAYLIST");
  if (!ownDone || syncStop || ringtonesId.isEmpty()) return;
  const char *own = syncState;
  syncPlaylist(ringtonesId, kRingtonesPlaylist, true);
  if (!strcmp(syncState, "UP TO DATE")) syncState = own;  // The owner's songs are what STATUS talks about.
}

void syncTask(void *) {
  os::bindTask(os::Owner::Music);
  // The whole pass holds the connection broker: started only when it has room (main loop checks),
  // so a socket here never meets a TLS handshake's RAM, and chat waits out one short pass.
  try {
    os::PlainLease lease(os::Priority::Background);
    if (lease.held) syncPass(); else syncState = "BUSY";
  } catch (const std::bad_alloc &) { syncState = "OUT OF MEMORY"; os::recordFault(os::Owner::Music, "card sync out of memory"); }
  syncHandle = nullptr;
  vTaskDelete(nullptr);
}
}  // namespace

void startCardSync() {
  if (syncHandle) return;
  syncStop = false;
  xTaskCreatePinnedToCore(syncTask, "cardsync", 8192, nullptr, 1, &syncHandle, 0);
}

void stopCardSync(uint32_t waitMs) {
  if (!syncHandle) return;
  syncStop = true;
  const uint32_t started = millis();
  while (syncHandle && millis() - started < waitMs) vTaskDelay(pdMS_TO_TICKS(20));
}

bool cardSyncRunning() { return syncHandle != nullptr; }

void cardSyncText(char *text, size_t size) {
  const char *state = syncState;
  if (!strcmp(state, "GETTING") || !strcmp(state, "MORE TO GET")) snprintf(text, size, "GETTING %d OF %d", syncDone + 1, syncMissing);
  else if (!strcmp(state, "LYRICS")) snprintf(text, size, "%d SONGS, GETTING LYRICS", syncTotal);
  else if (!strcmp(state, "UP TO DATE") && syncTotal >= 0) snprintf(text, size, "%d SONGS, UP TO DATE", syncTotal);
  else snprintf(text, size, "%s", state);
}

int takeCardSyncAdded() { const int added = syncAdded; syncAdded = 0; return added; }

bool isOwnSongsFolder(const String &folderName) { return kUser[0] && folderName.equalsIgnoreCase(kUser); }
bool isRingtonesFolder(const String &folderName) { return folderName == kRingtonesPlaylist; }

String takeCardSyncAddedNames() {
  String names = syncAddedNames;
  if (syncAdded > syncNamesListed) names += " +" + String(syncAdded - syncNamesListed);
  syncAddedNames = ""; syncNamesListed = 0;
  return names;
}

bool begin() {
  // Wanted again before it had ended: it just carries on.
  if (taskHandle) { endRequested = false; return true; }
  if (!mutex) mutex = xSemaphoreCreateMutex();
  return xTaskCreatePinnedToCore(workerTask, "library", 12288, nullptr, 1, &taskHandle, 0) == pdPASS;
}

void end() { if (taskHandle) endRequested = true; }

bool configured() { return kHost[0] != 0; }

void requestAlbums(Collection collection, int offset) { enqueue({JobKind::Albums, "", offset, collection}, true); }
void requestCardAlbums(Collection collection) { enqueue({JobKind::CardAlbums, "", 0, collection}, true); }
void requestCardTracks(const String &folder) { enqueue({JobKind::CardTracks, folder, 0, Collection::Books}, true); }
void saveCardPlace(const String &folder, const String &trackPath, uint32_t seconds) { enqueue({JobKind::SavePlace, folder, static_cast<int>(seconds), Collection::Books, trackPath}, false); }
void clearCardPlace(const String &folder) { enqueue({JobKind::ClearPlace, folder, 0, Collection::Books}, false); }
void requestAdjacentTrack(const String &folder, const String &title, int direction) { enqueue({JobKind::Adjacent, folder, direction, Collection::Books, title}, false); }
bool takeAdjacentTrack(Item &item, bool &found) {
  if (!mutex) return false;
  xSemaphoreTake(mutex, portMAX_DELAY);
  const bool ready = adjacentReady;
  if (ready) { item = adjacentItem; found = adjacentFound; adjacentReady = false; }
  xSemaphoreGive(mutex);
  return ready;
}
void requestLiked() { enqueue({JobKind::Liked, "", 0, Collection::Music}, true); }
void requestSearch(const String &query, int offset) { enqueue({JobKind::Search, query, offset, Collection::Music}, true); }
void requestTracks(const String &albumId) { enqueue({JobKind::Tracks, albumId, 0, Collection::Music}, true); }
void saveBookmark(const String &trackId, uint32_t positionMs) { enqueue({JobKind::SaveBookmark, trackId, static_cast<int>(positionMs), Collection::Books}, false); }
void deleteBookmark(const String &trackId) { enqueue({JobKind::DeleteBookmark, trackId, 0, Collection::Books}, false); }
void setLiked(const String &trackId, bool liked) { enqueue({liked ? JobKind::Like : JobKind::Unlike, trackId, 0, Collection::Music}, false); }

void forget() { wantedGeneration = wantedGeneration + 1; if (currentStatus == Status::Loading || currentStatus == Status::Failed) currentStatus = Status::Idle; }

Status status() { return currentStatus; }
const char *failure() { return failureText; }

bool takeItems(std::vector<Item> &items) {
  if (!mutex) return false;
  xSemaphoreTake(mutex, portMAX_DELAY);
  const bool fresh = itemsFresh;
  if (fresh) { items = std::move(finished); finished.clear(); itemsFresh = false; }
  xSemaphoreGive(mutex);
  return fresh;
}

String streamUrl(const String &trackId, Collection collection, uint32_t offsetSeconds) {
  String url = apiUrl("stream") + "&id=" + urlEncode(trackId) + "&format=mp3&maxBitRate=" + (collection == Collection::Books ? kBooksKbps : kMusicKbps);
  if (offsetSeconds) url += String("&timeOffset=") + offsetSeconds;
  return url;
}

}  // namespace library

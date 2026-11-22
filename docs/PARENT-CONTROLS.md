# Parent controls

Jay's design, dictated 2026-11-22, with his corrections from the same morning. The parent concern is
distraction at night. The Cardputer charges beside the kid, so it must stay a good clock and alarm while
the rest goes quiet.

Status at the end of this document: what is built, what is tested, what is next.

## Did we already have this? (audit, 2026-11-22)

| Piece | Before this work | Where |
|---|---|---|
| Groups and members | **Yes.** `user_groups`, `user_group_members`; every room belongs to a group; group members see all its rooms. | `server/src/server.mjs` (schema, `/api/admin/groups`) |
| Invite link + QR code | **Yes, admin only.** One-use Authentik enrollment link for a named username, joins chosen groups, returns a QR data URL. Shown in the portal's admin zone. | `server.mjs` `/api/admin/invitations`; `server/web/app.js` (`#invite-form`); `server/deploy/configure-invite-service.py` |
| Invite that prompts the app install | **No.** The link goes to Authentik, then the web portal. The Android app has the `supachat` scheme but no https App Links and no iOS bundle id. | `JayPloss/supachat-app` `app.json` |
| Parent / manager role | **No.** Only `users.role` = `admin` or `member` (Papa is admin). Parents' devices differ from kids' only by build flag. | `server.mjs`; `firmware/src/main.cpp` (`SUPACHAT_DEVICE_PAPA`/`MAMA`) |
| Per-device settings | **Barely.** `device_preferences` holds a language override only. | `server.mjs` |
| Night limits | **Yes, hard-coded in firmware, same for every kid.** Bedtime 19:00-05:00 (books get a 30-min sleep timer, idle clock dims); school nights Sun-Thu a playing book is warned at 21:20 and stopped at 21:30. Off on Papa's and Mama's builds. | `firmware/src/apps/time/clock_apps.h` (`isBedtime`, `schoolNightStopInMs`); `main.cpp` (`kSchoolNightRules`) |
| Screen-time timer | **Yes, on the device**, set on the device (up to 120 min). | `clock_apps.h` (`ScreenTime`), `main.cpp` |
| Clock | SNTP (unauthenticated), seeded from the build time until it answers; nothing rings or dims until SNTP answers (`wallClock` needs `clockFromNtp`). **No manual clock setting exists on the device.** No external RTC chip is used. | `main.cpp` (`ensureTime`, `seedBuildTime`, `wallClock`) |
| Offline chat | **Yes.** Typed messages wait in a persistent outbox in NVS until the server confirms them. | `firmware/src/apps/chat/outbox.h`, `main.cpp` |

So: invites with QR codes, yes (admin only, not install-aware). Parents, per-kid policy, blackout windows,
and clock protection, no.

## Principle: off Wi-Fi is normal

Jay, 2026-11-22: **being off Wi-Fi is a feature, not a bug.** Wi-Fi is needed only to move data between
distant places (chat, sync). Clock, alarms, timers, screen time and blackout windows all work from the
device alone. (Also in `AGENTS.md`.)

- The policy is cached on the device and enforced from the device's own clock. Nothing in the enforcement
  path waits for the server. "Could not reach the server" never means "no limits".
- Chat queues offline and sends when a network appears; nothing is dropped. Reports and receipts too.
- Anything that needs the network says so in one line on screen, and the rest keeps working.
- **But not aggressive.** A wrong clock must never stop a kid playing music on a car trip. Predictable
  beats unbreakable: most kids don't try to cheat a limit that mostly just works.

## Data model (server)

Built in `server/src/parent-controls.mjs`. New tables only; nothing existing changes shape.

- **Parent = guardian of a kid.** `guardians(parent_id, kid_id)`. A kid has one or more parents; one
  account can be a parent of kids in several groups. Parents are web/app accounts, never devices.
- **Scope rule** (Jay): a parent manages *their own kids, plus any kid one of their kids brought in*.
  `kid_links(kid_id, invited_by_kid)` records who brought whom (written when a kid's invite is claimed).
  `kidsInScope()` = own kids + kids linked to them. The admin sees all devices.
- **Kid = a device account** (`users.kind = 'device'`); the policy is per device. Today one kid = one
  device account. A kid who joins through the app (no Cardputer yet) is a web account; when they get a
  device, the admin links it (follow-on).
- **Policy** `device_policies(user_id, blackouts, clock_override_enabled, device_settings_enabled,
  parent_code_hash, updated_at, updated_by)`.
  - `blackouts`: up to 12 windows `{label, days[0-6], start "HH:MM", end "HH:MM", features[]}`.
    Features that can be switched off: `chat, walkie, music, books, podcasts`. Always allowed, never in a
    window: `clock, alarms, reminders, timers, screen_time`. A window that crosses midnight belongs to the
    day it starts (Sun 21:00-07:00 covers Monday 06:59). Reference function `blockedFeatures()`.
  - **Parent code**: 6-8 digits, one per device, stored as PBKDF2-SHA256 (20 000 rounds, per-device salt).
    It unlocks what the parent enables: changing the device clock (`clock_override_enabled`) and parent
    mode on the device (`device_settings_enabled`). Either needs a code set.
- **Person invites** `person_invites(id, token_hash, kind parent|kid, display_name, default_username,
  chosen_username, group_id, for_kid, created_by, expires_at, claimed_at, claimed_by, revoked_at)`.
  One person per invite, 14 days. Only the token's hash is stored.

## Invite flow

1. **Made by a parent** (portal/app: "Invite someone for this kid": another parent, or a friend) **or by a
   kid** (on the Cardputer: types who they're sharing with). The name typed becomes the default username:
   `Marie Dupont` -> `marie-d`, then `marie-d2` if taken (checked against accounts and open invites).
   The username is reserved at that moment: the account exists in advance, waiting for a password.
2. The invite is a link `https://supachat.net/join/<token>`, shown as a QR code (portal; on the Cardputer
   the device draws the QR itself). A kid can take it home from school: "take this home, scan it".
3. **Phone scans it**: the join page names who invited them and to what, offers the app install for that
   phone (Android/iOS URLs from `SUPACHAT_ANDROID_INSTALL_URL` / `SUPACHAT_IOS_INSTALL_URL`; none set
   means web only), and shows the reserved username.
   **Computer opens it**: the same page, without the install button.
4. They keep the username or change it **once**, then "Set my password": the server mints a one-use
   Authentik enrollment for that exact username, whose `next` brings them back to `/join/<token>?done=1`.
   The invite is bound to its token, not the username, so a rename can't break it.
5. Back on the join page, signed in, it claims the invite: joins the group; a parent invite makes them a
   parent of that kid; a kid's invite records "brought in by" that kid, which puts the new kid in the
   inviting kid's parents' scope. "I already have an account" claims it with the existing account.
6. The app should open these links itself (Android App Links / iOS universal links for `/join/*`) and
   run step 4-5 natively. Until then the web page does it all.

## Enforcement on the device (firmware plan; not built)

**Where the policy comes from.** `GET /api/device/policy` returns `{payload, sig}`: `payload` is the exact
JSON string (user, `server_time`, `issued_at`, `valid_until`, tz, blackouts, flags, the parent code's
PBKDF2 verifier, the clock-trust thresholds below); `sig` = HMAC-SHA256 keyed with sha256(device token),
which the device can compute and the server already stores. The device keeps the newest verified envelope
in NVS and enforces it from then on, online or not. `valid_until` (30 days) only makes the status line say
"rules not refreshed"; it never lifts a rule.

**Check-ins.** At boot once Wi-Fi is up, then every 20 minutes while on a known network. The chat sync
already returns `server_time` on every poll over the pinned TLS connection; that counts as a sync too.

**Clock trust tiers** (thresholds sent in the policy as `clock_trust`, tunable without a build):

| Tier | When | Blackout windows | Screen |
|---|---|---|---|
| A, synced | Server time received in the last **6 h** | Enforced exactly | - |
| B, plausible | Last sync **6-72 h** ago, clock not behind the high-water mark | Enforced, each window starting **10 min** late so nothing is cut mid-song | - |
| C, unknown | Never synced since power-on, or last sync **over 72 h** ago | **Not enforced** for music, books, podcasts. Chat and walkie need Wi-Fi, and Wi-Fi brings a sync, so they are back in tier A whenever they could work | One line: "Clock needs Wi-Fi to sync" |

Back to tier A on the next sync. Alarms, timers and the clock keep working in every tier (alarms already
wait for a real time today; they keep doing so).

**High-water mark, against rollback only.** Every 10 minutes and before sleep the device writes the
latest trusted time to NVS. If the clock is ever found more than **2 minutes** behind that mark, it is
moved forward to the mark: moving the clock back can't reopen a closed window. It never blocks anything
by itself. Time from the server moves the clock either way (it is authoritative) and resets the mark, so
an honest fast clock is corrected by the next sync.

**Manual clock change.** None exists today. If added, only behind the parent code and only when
`clock_override` is on; a parent-set time counts as tier B and resets the mark.

**Parent mode on the device** (last resort, the screen is tiny). Behind the parent code, only when
`device_settings` is on. It edits the same blackout objects. The device checks the code against the
cached verifier (so it works offline, with a lockout after 5 wrong tries), applies the change at once,
and sends it with the code to `PUT /api/device/policy`, where the server checks the code again.

**What gets gated.** Opening Music, Books, podcasts, SupaChat send and walkie during an active window:
a one-line "Quiet time until 07:00" instead. Something already playing when a window starts gets the
existing warn-then-stop treatment (the school-night stop becomes a case of this). Incoming chat during a
window is stored silently and shown after.

## Management surfaces

- **Web portal `/manage`** (built): each kid in scope, quiet-time windows, the parent-code toggles and code,
  and invites with QR. Works at phone width.
- **Android/iOS app** (`supachat-app`, not built): the same API (`/api/manage/*`) with the native session;
  plus the App Links for `/join/*`.
- **On the Cardputer**: parent mode behind the code, as above. Management calls (`/api/manage/*`) refuse a
  device token.

## Trust model: what a determined 13-year-old can do

Realistic guarantee: **a kid who uses the device normally gets the quiet times their parents set, every
night, with or without Wi-Fi.** It is not a lock against a kid with a laptop and a free afternoon.

- **Change the clock?** No clock setting exists, and the high-water mark stops rolling it back. Fine.
- **Stay off Wi-Fi?** Windows keep working from the cached policy for 72 h after the last sync. After that,
  or after a full power-off (battery dead or the power switch held off, since there is no battery-backed RTC
  chip), the clock is unknown and media plays freely, by Jay's rule. Chat and walkie are unusable offline
  anyway. A kid who powers the device off and on while away from Wi-Fi can play music at night. Accepted:
  predictability over enforcement. A soft reset keeps the time.
- **Fake the time server?** SNTP is unauthenticated: someone controlling the home network could feed a
  false time. Fix: trust SNTP only when it agrees with the server's time within 2 min (follow-on).
- **Flash other firmware?** Yes. Anyone can flash a Cardputer over USB-C with free tools; we don't enable
  secure boot or flash encryption (they are permanent eFuse writes and would complicate our own updates).
  The server would see the device go silent (`presence.last_seen_at`). Detection, not prevention; showing
  "last seen" to parents is a follow-on.
- **Read the parent code?** Only by dumping flash (USB, tools, know-how), then brute-forcing a 6-8 digit
  PBKDF2 hash offline. Possible for a determined kid. A parent who worries uses 8 digits and changes it.
- **Guess the parent code on the device?** Lockout after 5 tries (planned).
- **Use a friend's device or the web portal?** Out of scope: the policy is per device.
- **Steal an invite?** It is one person, one use, 14 days, revocable; the person still sets a password.

## Built in this slice (2026-11-22)

Server (`server/src/parent-controls.mjs`, wired into `server.mjs` with an import, one init call, one
handler call before the authenticated routes, and a `mintEnrollment` helper; the existing admin invite and
message paths are unchanged):

- `GET /api/manage/kids`, `PUT /api/manage/kids/:id/policy`, `PUT|DELETE /api/manage/kids/:id/guardians/:user`,
  `POST /api/manage/invites`, `DELETE /api/manage/invites/:id`
- `GET /join/:token`, `GET /api/join/:token`, `POST /api/join/:token/enroll`, `POST /api/join/:token/accept`
- `GET /api/device/policy` (signed envelope), `PUT /api/device/policy` (parent mode, code required),
  `POST /api/device/invites` (a kid shares from the device; returns the link and username, no image)
- Pages `server/web/manage.html|js|css`, `server/web/join.html|js`

**Tested** (`server/test/parent-controls.test.mjs`, in `npm test`; 10 tests, plus the 33 existing ones
still pass): window maths including overnight and the morning after; validation; code hashing; default
usernames; scope (no kids -> 403; admin adds a parent; the parent edits; a device can't manage); the
signed envelope verified with the device's key; parent mode with wrong and right code; a kid's invite ->
rename once -> second rename refused -> claim -> one use only -> the inviting kid's parent sees the new
kid; a parent can invite a co-parent only for their own kid. Both pages driven in headless Chromium against
a scratch server at desktop and phone width (save, invite, QR, Android landing): no script errors.

**Not tested / not done**: not deployed to supachat.net. The real Authentik enrollment with the minted
invitation and the `next` return was **not** exercised (tests use the server's test stub); per this repo's
authentication rules, joining is **unproven** until a real person completes it. `/join/*` and `/api/join/*`
must be reachable without signing in: the production proxy config (not in this repo) has to let them
through. No firmware changes. No app changes.

## Next steps, with effort

1. **Deploy and prove the join flow** (half a day): proxy rule for `/join/*` and `/api/join/*`, set the
   Android install URL, run one real invite end to end on a phone and on a computer, and seed `guardians`
   for the current families (who is whose parent is Jay's call; the admin adds them with
   `PUT /api/manage/kids/:kid/guardians/:parent`).
2. **Firmware: cached policy + gating** (2-3 days): fetch and verify the envelope, keep it in NVS, gate the
   apps by window, one-line "Quiet time until …", replace the hard-coded bedtime/school-night rules with
   the policy (seed each kid's policy with today's rules first so nothing changes for them). Host tests of
   the window maths against the server's `blockedFeatures()`, then `cardputer-emulator` scenarios.
3. **Firmware: clock trust** (1-2 days): tiers A/B/C, high-water mark, server time as a sync, SNTP only when
   it agrees with the server. **Measure the clock's drift in light sleep first** (no external RTC): if it
   drifts minutes per hour, tighten the 6 h tier-A window.
4. **Kid sharing on the Cardputer** (1 day): a "Share" screen (type a name -> QR on screen), using the
   device's QR library; and parent mode behind the code with lockout (1-2 days, design-reference-cardputer
   first).
5. **App** (2-3 days): Android App Links + iOS universal links for `/join/*`, the manage screens over
   `/api/manage/*`, and an iOS bundle id (iOS install needs App Store/TestFlight; see paqman notes on xtool).

Smaller: "last seen" and firmware version per kid on `/manage`; revoke buttons for pending invites; the
admin linking a kid's app account to their device; deciding what happens when a kid brought in by another
family also has their own parents (today both sets can edit the one policy of that device; the last save
wins).

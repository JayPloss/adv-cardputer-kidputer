Import("env")

import hashlib
import os
import subprocess
from SCons.Script import COMMAND_LINE_TARGETS


def _tree_fingerprint(repo, paths):
    """Everything a gate run depends on in one repo, by content: every tracked or untracked (not ignored)
    file under the paths, as it is on disk. A commit or an amend of the same code keeps the pass; the
    browser mock's screenshots (written by its visual test, never read by the gate) don't count."""
    names = subprocess.run(["git", "-C", repo, "ls-files", "-co", "--exclude-standard", "-z", "--", *paths],
                           capture_output=True, check=False).stdout.split(b"\0")
    digest = hashlib.sha256()
    for name in sorted(n for n in names if n):
        text = name.decode()
        # The screenshots aren't read by the gate; the gate's own script is the harness, not the code it passes.
        if text.startswith("emulator/screens/") or text == "firmware/tools/pre-upload-gate.py":
            continue
        path = os.path.join(repo, text)
        if not os.path.isfile(path):
            continue  # deleted but not yet committed
        digest.update(name + b"\0")
        with open(path, "rb") as handle:
            digest.update(hashlib.sha256(handle.read()).digest())
    return digest.hexdigest()


def gate_key(repo, runtime):
    digest = hashlib.sha256(_tree_fingerprint(repo, ["firmware", "emulator"]).encode())
    # Ignored but compiled in: the secrets headers.
    for name in sorted(os.listdir(os.path.join(repo, "firmware", "include"))):
        if "secrets" in name:
            with open(os.path.join(repo, "firmware", "include", name), "rb") as handle:
                digest.update(name.encode() + hashlib.sha256(handle.read()).digest())
    if os.path.isdir(runtime):
        digest.update(_tree_fingerprint(runtime, ["."]).encode())
    return digest.hexdigest()


# Where the runtime emulator can't model a device function, the gate overrides it, and only there (Jay,
# 2026-11-24). Each function's stand-in and the reason are in firmware/tools/not-emulated.h, which main.cpp
# includes in the emulator's build only. Keep this list and that file in step. Every run prints this list as "not covered": those layers are proven on a device or not at all.
NOT_EMULATED = {
    "esp_reset_reason": "reset cause from the chip's RTC domain; the emulator's clock always equals world time, "
                        "so a clock lost with the power (unknown tier, high-water mark) can't be shown",
    "gettimeofday": "the chip's clock, read through getLocalTime to the second, so the drift a sync logs is "
                    "whole seconds",
}
# Scenarios skipped because what they check depends on a function above, and why.
NOT_EMULATED_SCENARIOS = {
    "book-sleep": "its `clock` line moves the time with no time-server answer behind it (the SNTP stand-in asks only "
                  "when a device would); v0.66 trusts a clock for quiet-time rules only from an answer, so the bedtime "
                  "timer is off there",
    "school-night": "same as book-sleep, for the 21:30 school-night stop",
}


# Scenarios that talk to the real music server over the real network (cardputer-emulator README). A failure
# there can be the network, not the firmware: the gate runs it once more, and check.py skips it, saying so,
# when the server doesn't answer at all.
# notify-reboot too: the build's card sync reaches the real server, and a slow one holds the one secure
# connection, so chat's message comes late (Juju's flash, 2026-11-25: held over 80 s).
# places waits for the card to be free to write its brightness log, which a card sync can hold.
NETWORK_SCENARIOS = {"stream", "card-sync", "notify-reboot", "places"}


def runtime_scenarios(runtime):
    """check.py's scenarios, but the specific ones the list above overrides."""
    import re
    source = open(os.path.join(runtime, "tools", "check.py")).read()
    names = re.findall(r"'([a-z0-9-]+)'", re.search(r"^ALL = \[(.*?)\]", source, re.M | re.S).group(1))
    return [name for name in names if name not in NOT_EMULATED_SCENARIOS]


def run_scenarios(runtime, repo, names):
    """Runs scenarios through check.py, showing its output; returns {name: PASS, FAIL or SKIPPED}."""
    import re
    results = {}
    process = subprocess.Popen(["python3", os.path.join(runtime, "tools", "check.py"), *names], cwd=repo,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        print(line, end="", flush=True)
        found = re.match(r"^([a-z0-9-]+): (PASS|FAIL|SKIPPED)", line)
        if found and found.group(1) in names:
            results[found.group(1)] = found.group(2)
    process.wait()
    for name in names:
        results.setdefault(name, "FAIL")  # never reported: a crash of check.py itself
    return results


class Ledger:
    """What already passed for this exact code (gate_key), check by check, in .pio/gate-pass.json. A failed
    run keeps its passes, so the next run does only what didn't pass (Jay, 2026-11-25: a dropped connection
    to the music server cost Juju's flash a full 25-minute rerun)."""

    def __init__(self, path, key):
        import json
        self.path, self.key, self.passed = path, key, set()
        try:
            data = json.load(open(path))
            if key and data.get("key") == key:
                self.passed = set(data.get("passed", []))
        except (OSError, ValueError):
            pass

    def done(self, check):
        return check in self.passed

    def add(self, check):
        import json
        self.passed.add(check)
        if self.key:
            with open(self.path, "w") as handle:
                json.dump({"key": self.key, "passed": sorted(self.passed)}, handle)


def run_device_contract_gate(source, target, env):
    repo = os.path.abspath(os.path.join(env["PROJECT_DIR"], ".."))
    runtime = os.path.abspath(os.path.join(repo, "..", "cardputer-emulator"))
    # The gate's checks don't depend on which device is being flashed (they build their own variants),
    # so a pass for this exact code covers every device in the round.
    # One gate at a time: an upload while tools/gate/run-gate.py runs in the background waits for it, then
    # uses what it passed.
    import fcntl
    os.makedirs(os.path.join(env["PROJECT_DIR"], ".pio"), exist_ok=True)
    lock = open(os.path.join(env["PROJECT_DIR"], ".pio", "gate.lock"), "w")
    if not env.get("GATE_LOCK_HELD"):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Upload gate: a background gate run is going; waiting for it, then using what it passed.", flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
    key = gate_key(repo, runtime) if env["PIOENV"] != "power-lab" else None
    ledger = Ledger(os.path.join(env["PROJECT_DIR"], ".pio", "gate-pass.json"), key)
    full = os.path.isdir(runtime) and env["PIOENV"] != "power-lab"
    if ledger.done("all"):
        print("Upload gate: this exact code already passed; not running it again.")
        return
    if ledger.passed:
        print(f"Upload gate: {len(ledger.passed)} checks already passed for this exact code; running only the rest.")

    def run(check, command):
        if ledger.done(check):
            return
        if subprocess.run(command, cwd=repo, check=False).returncode:
            raise RuntimeError(f"Firmware upload blocked by emulator gate: {' '.join(command)}")
        ledger.add(check)

    run("device-contract", ["node", os.path.join(repo, "emulator", "device-contract.mjs")])
    run("test-flow", ["node", os.path.join(repo, "emulator", "test-flow.mjs")])
    # The runtime emulator, when it is checked out beside this repo: real firmware on a device-sized heap.
    # The power lab is its own program and has no scenarios.
    if full:
        print("Upload gate: not covered by the emulator (overridden, see firmware/tools/not-emulated.h):")
        for name, why in NOT_EMULATED.items():
            print(f"  - {name}: {why}")
        for name, why in NOT_EMULATED_SCENARIOS.items():
            print(f"  - scenario {name} skipped: {why}")
        todo = [name for name in runtime_scenarios(runtime) if not ledger.done("scenario:" + name)]
        failed = []
        if todo:
            results = run_scenarios(runtime, repo, todo)
            # The network ones that failed get one more run before they count.
            again = [name for name, result in results.items() if result == "FAIL" and name in NETWORK_SCENARIOS]
            if again:
                print(f"Upload gate: {', '.join(again)} talk to the real music server; running them once more.")
                results.update(run_scenarios(runtime, repo, again))
            for name, result in results.items():
                if result == "FAIL":
                    failed.append(name)
                else:
                    if result == "SKIPPED":
                        print(f"Upload gate: not covered this run: scenario {name} (skipped by check.py)")
                    ledger.add("scenario:" + name)
        if failed:
            raise RuntimeError(f"Firmware upload blocked by emulator gate: scenario {', '.join(failed)} failed; "
                               f"the {len(ledger.passed)} checks that passed won't run again for this code")
        # The real binary in QEMU: the tier that sees ESP-IDF startup, the real heap and newlib.
        run("qemu-boot", ["bash", os.path.join(runtime, "qemu", "boot-check.sh"), env["PROJECT_DIR"]])
        ledger.add("all")


def backup_device_settings(source, target, env):
    """Copy the device's settings partition (NVS: chat login, Wi-Fi, alarms, outbox) off the device before every
    flash, so a lost or broken Cardputer can be restored onto another one. Saved outside the repo (it holds secrets),
    under ~/Backups/cardputer-nvs/<name>/. A device that can't be read blocks the flash."""
    import datetime, re
    port = env.subst("$UPLOAD_PORT")
    if not port or not os.path.exists(port):
        print(f"Settings backup: no device at {port or '(no port)'}; nothing to back up.")
        return
    esptool = [env.subst("$PYTHONEXE"), os.path.join(env.PioPlatform().get_package_dir("tool-esptoolpy"), "esptool.py"),
               "--chip", "esp32s3", "--port", port]
    mac = subprocess.run(esptool + ["--before", "default_reset", "--after", "no_reset", "read_mac"],
                         capture_output=True, text=True)
    found = re.search(r"MAC: ([0-9a-f:]{17})", mac.stdout)
    if not found:
        raise RuntimeError(f"Settings backup: couldn't read the device's MAC on {port}; flash blocked.\n{mac.stdout}{mac.stderr}")
    mac = found.group(1)
    names = os.path.join(env["PROJECT_DIR"], "..", "skills", "flash-cardputer-safely", "scripts", "resolve-device.ps1")
    known = {m.lower(): n for m, n in re.findall(r"'([0-9A-Fa-f:]{17})'\s*=\s*@\{\s*Name='([^']+)'", open(names).read())}
    name = known.get(mac, "unknown")
    folder = os.path.expanduser(os.path.join("~", "Backups", "cardputer-nvs", name))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out = os.path.join(folder, f"{stamp}-{mac.replace(':', '')}-before-{env['PIOENV']}.bin")
    read = subprocess.run(esptool + ["--baud", "921600", "--before", "no_reset", "--after", "no_reset",
                                     "read_flash", "0x9000", "0x5000", out], capture_output=True, text=True)
    if read.returncode or not os.path.exists(out) or os.path.getsize(out) != 0x5000:
        raise RuntimeError(f"Settings backup: reading settings from {name} ({mac}) failed; flash blocked.\n{read.stderr}")
    os.chmod(out, 0o600)
    print(f"Settings backup: {name} ({mac}) saved to {out}")


if any(target in COMMAND_LINE_TARGETS for target in ("upload", "program")):
    env.AddPreAction("upload", run_device_contract_gate)
    env.AddPreAction("upload", backup_device_settings)

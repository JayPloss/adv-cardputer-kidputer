Import("env")

import hashlib
import os
import subprocess
from SCons.Script import COMMAND_LINE_TARGETS


def _tree_fingerprint(repo, paths):
    """Everything a gate run depends on in one repo: the committed tree, uncommitted edits, and
    untracked files (by content), so a pass is reused only for exactly the same code."""
    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=False).stdout
    digest = hashlib.sha256()
    for path in paths:
        digest.update(git("rev-parse", f"HEAD:{path}" if path != "." else "HEAD^{tree}"))
    digest.update(git("diff", "HEAD", "--", *paths))
    for name in git("ls-files", "--others", "--exclude-standard", "-z", "--", *paths).split(b"\0"):
        if name:
            digest.update(name)
            with open(os.path.join(repo, name.decode()), "rb") as handle:
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


def runtime_check_command(runtime):
    """check.py with every scenario but the specific ones the list above overrides."""
    command = ["python3", os.path.join(runtime, "tools", "check.py")]
    if NOT_EMULATED_SCENARIOS:
        import re
        source = open(os.path.join(runtime, "tools", "check.py")).read()
        names = re.findall(r"'([a-z0-9-]+)'", re.search(r"^ALL = \[(.*?)\]", source, re.M | re.S).group(1))
        command += [name for name in names if name not in NOT_EMULATED_SCENARIOS]
    return command


def run_device_contract_gate(source, target, env):
    repo = os.path.abspath(os.path.join(env["PROJECT_DIR"], ".."))
    runtime = os.path.abspath(os.path.join(repo, "..", "cardputer-emulator"))
    # The gate's checks don't depend on which device is being flashed (they build their own variants),
    # so a pass for this exact code covers every device in the round.
    stamp = os.path.join(env["PROJECT_DIR"], ".pio", "gate-pass")
    key = gate_key(repo, runtime) if env["PIOENV"] != "power-lab" else None
    if key and os.path.exists(stamp) and open(stamp).read().strip() == key:
        print("Upload gate: this exact code already passed; not running it again.")
        return
    checks = [
        ["node", os.path.join(repo, "emulator", "device-contract.mjs")],
        ["node", os.path.join(repo, "emulator", "test-flow.mjs")],
    ]
    # The runtime emulator, when it is checked out beside this repo: real firmware on a device-sized heap.
    # The power lab is its own program and has no scenarios.
    if os.path.isdir(runtime) and env["PIOENV"] != "power-lab":
        checks.append(runtime_check_command(runtime))
        print("Upload gate: not covered by the emulator (overridden, see firmware/tools/not-emulated.h):")
        for name, why in NOT_EMULATED.items():
            print(f"  - {name}: {why}")
        for name, why in NOT_EMULATED_SCENARIOS.items():
            print(f"  - scenario {name} skipped: {why}")
        # The real binary in QEMU: the tier that sees ESP-IDF startup, the real heap and newlib.
        checks.append(["bash", os.path.join(runtime, "qemu", "boot-check.sh"), env["PROJECT_DIR"]])
    for command in checks:
        completed = subprocess.run(command, cwd=repo, check=False)
        if completed.returncode:
            raise RuntimeError(f"Firmware upload blocked by emulator gate: {' '.join(command)}")
    if key and os.path.isdir(runtime):
        with open(stamp, "w") as handle:
            handle.write(key + "\n")


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

#!/usr/bin/env python3
"""Runs the firmware upload gate (firmware/tools/pre-upload-gate.py) without an upload, so a pass is
waiting when a device is plugged in: the upload then finds this exact code already passed and flashes at
once. Started in the background by tools/gate/post-commit after a commit that touches firmware/ or
emulator/ (install: tools/gate/install-hook.sh). One run at a time (a lock in .pio); the log is
firmware/.pio/gate-background.log.

  python3 -I tools/gate/run-gate.py          # gate the working tree now
"""
import fcntl
import os
import sys
import types

repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
firmware = os.path.join(repo, "firmware")
os.makedirs(os.path.join(firmware, ".pio"), exist_ok=True)
lock = open(os.path.join(firmware, ".pio", "gate.lock"), "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("Gate: another run is going; it covers this code if nothing changed after it started.")
    sys.exit(0)

# The gate is a PlatformIO pre-script: give it the two names it imports.
scons = types.ModuleType("SCons"); script = types.ModuleType("SCons.Script"); script.COMMAND_LINE_TARGETS = []
scons.Script = script; sys.modules["SCons"] = scons; sys.modules["SCons.Script"] = script
class Env(dict):
    def AddPreAction(self, *args):
        pass
env = Env(PROJECT_DIR=firmware, PIOENV="cardputer-adv", GATE_LOCK_HELD=True)
scope = {"Import": lambda name: None, "env": env, "__name__": "gate"}
exec(open(os.path.join(firmware, "tools", "pre-upload-gate.py")).read(), scope)
try:
    scope["run_device_contract_gate"](None, None, env)
except RuntimeError as error:
    print(f"GATE FAILED: {error}")
    sys.exit(1)
print("GATE PASS: an upload of this code flashes without running it again.")

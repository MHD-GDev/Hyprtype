#!/usr/bin/env python3
"""Manage Hyprtype's Hyprland push-to-talk integration safely."""
from __future__ import annotations
import argparse, json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dependencies import ensure_dependencies

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
HYPRTYPE_HOME = CONFIG_HOME / "hyprtype"
HYPR_HOME = CONFIG_HOME / "hypr"
STATE_FILE = HYPRTYPE_HOME / "hyprland" / "setup.json"
UI_FILE = HYPRTYPE_HOME / "ui.json"
UI_OPTIONS = {"normal": "Normal (GTK)", "qt": "Qt"}
LUA_FILE = HYPR_HOME / "hyprland.lua"
NATIVE_FILE = HYPR_HOME / "hyprland.conf"
LUA_BEGIN, LUA_END = "-- >>> HYPRTYPE MANAGED PUSH-TO-TALK >>>", "-- <<< HYPRTYPE MANAGED PUSH-TO-TALK <<<"
CONF_BEGIN, CONF_END = "# >>> HYPRTYPE MANAGED PUSH-TO-TALK >>>", "# <<< HYPRTYPE MANAGED PUSH-TO-TALK <<<"
DEFAULT_SHORTCUT = "ALT_R"
MODIFIERS = {"SUPER", "SHIFT", "CTRL", "ALT", "MOD2", "MOD3", "MOD5"}

def notify(message, urgency="normal"):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Hyprtype", "-u", urgency, "Hyprtype", message],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def find_hyprtype():
    found = shutil.which("hyprtype")
    if found:
        return str(Path(found).resolve())
    candidate = Path(__file__).resolve().parents[2] / "hyprtype"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("Cannot find the hyprtype command. Install Hyprtype or add it to PATH.")

def detect_backend():
    if not shutil.which("hyprctl"):
        raise RuntimeError("hyprctl is unavailable; Hyprtype's shortcut setup requires Hyprland.")
    probe = subprocess.run(["hyprctl", "instances"], capture_output=True, text=True)
    if probe.returncode or not probe.stdout.strip():
        raise RuntimeError("Hyprland is not running.")
    configured = os.environ.get("HYPRLAND_CONFIG")
    if configured:
        active = Path(configured).expanduser()
        if active.is_file():
            return ("lua" if active.suffix.lower() == ".lua" else "native"), active
        raise RuntimeError(f"HYPRLAND_CONFIG points to a missing file: {active}")
    # Hyprland's conventional native config takes precedence if both files exist.
    if NATIVE_FILE.is_file():
        return "native", NATIVE_FILE
    if LUA_FILE.is_file():
        return "lua", LUA_FILE
    raise RuntimeError("Could not find ~/.config/hypr/hyprland.lua or hyprland.conf. No configuration was changed.")

def parse_shortcut(value):
    parts = [part.strip().upper() for part in value.split("+") if part.strip()]
    if not parts:
        raise ValueError("Enter a key such as ALT_R or SUPER + SPACE.")
    key, mods = parts[-1], parts[:-1]
    if any(mod not in MODIFIERS for mod in mods):
        raise ValueError("Supported modifiers: SUPER, SHIFT, CTRL, ALT, MOD2, MOD3, MOD5.")
    if key in MODIFIERS or not re.fullmatch(r"[A-Z0-9_]+", key):
        raise ValueError("The final shortcut component must be a valid Hyprland key name.")
    if len(set(mods)) != len(mods):
        raise ValueError("A modifier was specified more than once.")
    return mods, key

def read_binds():
    result = subprocess.run(["hyprctl", "-j", "binds"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Could not inspect active Hyprland bindings: " + result.stderr.strip())
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hyprland returned invalid binding data; refusing to change shortcuts.") from exc
    if not isinstance(data, list):
        raise RuntimeError("Hyprland returned an unexpected binding list; refusing to change shortcuts.")
    return data

def legacy_hyprtype_pair(text, key):
    if key != "ALT_R":
        return False
    # Only recognize the original, unmarked pair. Never infer ownership from
    # a command string elsewhere in a generated Hyprtype block.
    unmanaged = re.sub(r"(?ms)^" + re.escape(LUA_BEGIN) + r".*?^" + re.escape(LUA_END) + r"\s*", "", text)
    start = any('hl.bind("ALT_R"' in line and "hyprtype --start" in line for line in unmanaged.splitlines())
    finish = any('hl.bind("ALT_R"' in line and "hyprtype --finish" in line for line in unmanaged.splitlines())
    return start and finish

def managed_hyprtype_pair(shortcut, backend, config_text):
    mods, key = parse_shortcut(shortcut)
    if backend == "lua":
        source = config_text
        begin, end = LUA_BEGIN, LUA_END
    else:
        bindings = HYPRTYPE_HOME / "hyprland" / "bindings.conf"
        source = bindings.read_text() if bindings.is_file() else ""
        begin, end = CONF_BEGIN, CONF_END
    match = re.search(r"(?ms)^" + re.escape(begin) + r"(.*?)^" + re.escape(end), source)
    if not match:
        return False
    block = match.group(1)
    if backend == "lua":
        expression = json.dumps(" + ".join(mods + [key]) if mods else key)
        return (any(f"hl.bind({expression}," in line and "--start" in line for line in block.splitlines())
                and any(f"hl.bind({expression}," in line and "--finish" in line for line in block.splitlines()))
    mod_expr = " ".join(mods)
    press = f"bind = {mod_expr}, {key}, exec,"
    release = f"bindr = {mod_expr}, {key}, exec,"
    return press in block and release in block

def find_conflicts(shortcut, config_text, backend):
    mods, key = parse_shortcut(shortcut)
    shortcut = " + ".join(mods + [key]) if mods else key
    legacy = (
        legacy_hyprtype_pair(config_text, key)
        or managed_hyprtype_pair(shortcut, backend, config_text)
    )
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            current_owned = (
                saved.get("status") == "enabled"
                and saved.get("shortcut") == shortcut
            )
            legacy = legacy or (current_owned and managed_hyprtype_pair(shortcut, backend, config_text))
        except (OSError, json.JSONDecodeError):
            pass
    legacy_release_slots = {False, True} if legacy else set()
    conflicts = []
    for bind in read_binds():
        if str(bind.get("key", "")).upper() != key:
            continue
        if legacy and bind.get("release", False) in legacy_release_slots:
            # Migrate exactly one press and one release binding from the known
            # legacy Hyprtype pair. Any additional binding remains a conflict.
            legacy_release_slots.remove(bind.get("release", False))
            continue
        conflicts.append(bind)
    return conflicts

def managed_block(shortcut, executable, backend):
    mods, key = parse_shortcut(shortcut)
    start = shlex.quote(executable) + " --start"
    finish = shlex.quote(executable) + " --finish"
    if backend == "lua":
        key_expr = json.dumps(" + ".join(mods + [key]) if mods else key)
        return "\n".join([
            LUA_BEGIN,
            "-- Generated by hyprtype-setup. Change it through hyprtype-setup.",
            f"hl.bind({key_expr}, hl.dsp.exec_cmd({json.dumps(start)}))",
            f"hl.bind({key_expr}, hl.dsp.exec_cmd({json.dumps(finish)}), {{ release = true }})",
            LUA_END,
        ])
    mod_expr = " ".join(mods)
    return "\n".join([
        CONF_BEGIN,
        "# Generated by hyprtype-setup. Change it through hyprtype-setup.",
        f"bind = {mod_expr}, {key}, exec, {start}",
        f"bindr = {mod_expr}, {key}, exec, {finish}",
        CONF_END,
    ])

def replace_managed_block(text, block, backend):
    begin, end = (LUA_BEGIN, LUA_END) if backend == "lua" else (CONF_BEGIN, CONF_END)
    text = re.sub(r"(?ms)^" + re.escape(begin) + r".*?^" + re.escape(end) + r"\s*", "", text)
    if backend == "lua":
        text = "\n".join(
            line for line in text.splitlines()
            if not ('hl.bind("ALT_R"' in line and ("hyprtype --start" in line or "hyprtype --finish" in line))
        ).rstrip() + "\n"
    return text.rstrip() + ("\n\n" + block + "\n" if block else "\n")

def ensure_native_source(config):
    bindings = HYPRTYPE_HOME / "hyprland" / "bindings.conf"
    source = f"source = {bindings}"
    text = config.read_text()
    if source in text:
        return text, False
    return text.rstrip() + "\n\n# Hyprtype managed integration\n" + source + "\n", True

def backup_config(path):
    if not path.is_file():
        return None
    backup = path.with_name(path.name + ".hyprtype-backup")
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup

def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".hyprtype-tmp")
    temp.write_text(content)
    os.chmod(temp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
    os.replace(temp, path)

def selected_ui():
    try:
        value = json.loads(UI_FILE.read_text()).get("ui", "normal")
        return value if value in UI_OPTIONS else "normal"
    except (OSError, json.JSONDecodeError, AttributeError):
        return "normal"

def save_ui(value):
    if value not in UI_OPTIONS:
        raise ValueError("Choose either normal or qt.")
    write_atomic(UI_FILE, json.dumps({"ui": value}, indent=2) + "\n")

def save_state(backend, config, shortcut, status, detail=""):
    data = {"version": 1, "backend": backend, "config": str(config),
            "shortcut": shortcut, "status": status, "detail": detail}
    write_atomic(STATE_FILE, json.dumps(data, indent=2) + "\n")

def apply(shortcut, auto=False):
    mods, key = parse_shortcut(shortcut)
    shortcut = " + ".join(mods + [key]) if mods else key
    backend, config = detect_backend()
    old_text = config.read_text()
    conflicts = find_conflicts(shortcut, old_text, backend)
    if conflicts:
        # If Hyprtype was previously active, remove only its managed binding so
        # the selected conflicting key leaves Hyprtype disabled as requested.
        if backend == "lua":
            disabled_text = replace_managed_block(old_text, "", backend)
            if disabled_text != old_text:
                backup_config(config)
                write_atomic(config, disabled_text)
        else:
            bindings_file = HYPRTYPE_HOME / "hyprland" / "bindings.conf"
            if bindings_file.exists():
                write_atomic(bindings_file, "")
        subprocess.run(["hyprctl", "reload"], capture_output=True, text=True)
        detail = f"{len(conflicts)} existing Hyprland binding(s) use {shortcut}."
        save_state(backend, config, shortcut, "conflict", detail)
        message = (f"{shortcut} is already in use. Hyprtype's shortcut is disabled; "
                   "your existing binding was not changed. Run hyprtype-setup to choose another key.")
        print(message)
        notify(message, "warning")
        return 1
    executable = find_hyprtype()
    block = managed_block(shortcut, executable, backend)
    native_bindings = HYPRTYPE_HOME / "hyprland" / "bindings.conf"
    previous_bindings = native_bindings.read_text() if backend == "native" and native_bindings.exists() else None
    if backend == "lua":
        updated = replace_managed_block(old_text, block, backend)
        if updated != old_text:
            backup_config(config)
            write_atomic(config, updated)
    else:
        updated = replace_managed_block(old_text, "", backend)
        include_text, changed = ensure_native_source(updated)
        if changed:
            backup_config(config)
            write_atomic(config, include_text)
        write_atomic(HYPRTYPE_HOME / "hyprland" / "bindings.conf", block + "\n")
    result = subprocess.run(["hyprctl", "reload"], capture_output=True, text=True)
    if result.returncode:
        write_atomic(config, old_text)
        if backend == "native":
            if previous_bindings is None:
                native_bindings.unlink(missing_ok=True)
            else:
                write_atomic(native_bindings, previous_bindings)
        save_state(backend, config, shortcut, "error", result.stderr.strip())
        raise RuntimeError("Hyprland rejected the integration; previous config restored. " + result.stderr.strip())
    save_state(backend, config, shortcut, "enabled")
    message = f"Push-to-talk configured: {shortcut}. Hold to dictate; release to type."
    print(message)
    notify(message)
    return 0

def status():
    if not STATE_FILE.exists():
        print("Hyprtype Hyprland integration is not configured.")
        return 1
    data = json.loads(STATE_FILE.read_text())
    print(f"Status: {data.get('status', 'unknown')}")
    print(f"Shortcut: {data.get('shortcut', 'none')}")
    print(f"Backend: {data.get('backend', 'unknown')}")
    print(f"Interface: {UI_OPTIONS[selected_ui()]}")
    if data.get("detail"):
        print(f"Details: {data['detail']}")
    return 0 if data.get("status") == "enabled" else 1

def uninstall():
    if not STATE_FILE.exists():
        print("No Hyprtype integration state was found.")
        return 0
    data = json.loads(STATE_FILE.read_text())
    backend, config = data.get("backend"), Path(data.get("config", ""))
    if config.is_file():
        old = config.read_text()
        updated = replace_managed_block(old, "", backend)
        if backend == "native":
            source = f"source = {HYPRTYPE_HOME / 'hyprland' / 'bindings.conf'}"
            updated = re.sub(r"(?m)^# Hyprtype managed integration\n" + re.escape(source) + r"\n?", "", updated)
        if updated != old:
            backup_config(config)
            write_atomic(config, updated)
    (HYPRTYPE_HOME / "hyprland" / "bindings.conf").unlink(missing_ok=True)
    STATE_FILE.unlink(missing_ok=True)
    if shutil.which("hyprctl"):
        subprocess.run(["hyprctl", "reload"], capture_output=True)
    print("Hyprtype's managed Hyprland integration has been removed.")
    notify("Hyprtype's Hyprland shortcut integration was removed.")
    return 0

def interactive():
    print("Hyprtype setup")
    print("Choose the interface to use when launching Hyprtype:")
    current_ui = selected_ui()
    print(f"  1. Normal (GTK){' [current]' if current_ui == 'normal' else ''}")
    print(f"  2. Qt{' [current]' if current_ui == 'qt' else ''}")
    choice = input(f"Interface [ {'1' if current_ui == 'normal' else '2'} ]: ").strip()
    if choice:
        choices = {"1": "normal", "normal": "normal", "gtk": "normal", "2": "qt", "qt": "qt"}
        if choice.lower() not in choices:
            print("Invalid interface selection. Setup cancelled.", file=sys.stderr)
            return 2
        current_ui = choices[choice.lower()]
    save_ui(current_ui)
    print(f"Interface selected: {UI_OPTIONS[current_ui]}")
    print("Now choose a push-to-talk shortcut, such as ALT_R, SUPER + SPACE, or CTRL + ALT + D.")
    current = DEFAULT_SHORTCUT
    if STATE_FILE.exists():
        try:
            current = json.loads(STATE_FILE.read_text()).get("shortcut", current)
        except Exception:
            pass
    try:
        value = input(f"Push-to-talk shortcut [{current}]: ").strip() or current
        return apply(value)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        return 130
    except (ValueError, RuntimeError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1

def main():
    parser = argparse.ArgumentParser(prog="hyprtype-setup", description="Configure Hyprtype push-to-talk for Hyprland.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--auto", action="store_true", help="First-launch setup using Right Alt if available.")
    group.add_argument("--status", action="store_true", help="Show integration status.")
    group.add_argument("--remove", action="store_true", help="Remove Hyprtype's managed integration.")
    group.add_argument("--set", metavar="SHORTCUT", help="Set a shortcut, e.g. 'SUPER + SPACE'.")
    group.add_argument("--repair", action="store_true", help="Reapply the currently selected shortcut.")
    group.add_argument("--dependencies", action="store_true", help="Install or repair Whisper.cpp and its model.")
    args = parser.parse_args()
    try:
        if args.status: return status()
        if args.remove: return uninstall()
        ensure_dependencies(interactive=sys.stdin.isatty())
        if args.dependencies: return 0
        if args.set: return apply(args.set)
        if args.repair:
            current = DEFAULT_SHORTCUT
            if STATE_FILE.exists():
                current = json.loads(STATE_FILE.read_text()).get("shortcut", current)
            return apply(current)
        if args.auto:
            if STATE_FILE.exists(): return 0
            if not UI_FILE.exists(): save_ui("normal")
            return apply(DEFAULT_SHORTCUT, auto=True)
        return interactive()
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"hyprtype-setup: {exc}", file=sys.stderr)
        notify(str(exc), "critical")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

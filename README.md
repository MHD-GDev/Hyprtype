# Sonora

**Local voice dictation for Linux.** Speak, transcribe with Whisper.cpp, and type the result directly into the focused application.

## Requirements

- Hyprland on Wayland
- PipeWire (pw-record)
- WirePlumber (wpctl)
- libnotify (notify-send)
- wtype for Wayland text input
- GTK 3 and GTK Layer Shell (primary recording orb UI)
- PySide6 / Qt 6 (optional alternative UI, launched with sonora-qt)
- Whisper.cpp at vendor/whisper.cpp/build/bin/whisper-cli
- English Whisper model at vendor/whisper.cpp/models/ggml-base.en.bin

On Arch Linux, install the optional Qt interface with sudo pacman -S pyside6. From the project directory, add the main commands to your PATH:

```bash
mkdir -p ~/.local/bin
ln -sfn "$(realpath ./sonora)" ~/.local/bin/sonora
ln -sfn "$(realpath ./sonora-setup)" ~/.local/bin/sonora-setup
```

Launch the GTK interface with sonora; launch the separate Qt interface with ./sonora-qt from the project directory.

## Usage

Run sonora to open the recording orb. Hold the configured push-to-talk key while speaking; release it to transcribe and type the result into the focused application. Press Ctrl+C in a terminal-launched session to cancel.

Sonora does not press Enter after inserting the transcription. Recordings and temporary state are stored under $XDG_RUNTIME_DIR/sonora-dictation (or /tmp/sonora-dictation when XDG_RUNTIME_DIR is unset).

## Hyprland shortcut setup

On the first launch inside Hyprland, Sonora automatically attempts to register **Right Alt** as its push-to-talk shortcut. If Right Alt is already in use, Sonora leaves the existing binding untouched and keeps its own shortcut disabled.

Run sonora-setup to choose or change the shortcut:

```bash
sonora-setup
```

You can also set a shortcut directly:

```bash
sonora-setup --set "SUPER + SPACE"
sonora-setup --status
sonora-setup --repair
sonora-setup --remove
```

The setup tool supports Hyprland's Lua configuration (~/.config/hypr/hyprland.lua) and native configuration (~/.config/hypr/hyprland.conf). It backs up the original configuration as a .sonora-backup file, maintains a clearly marked Sonora-owned section, checks active bindings before registration, and never replaces another application's shortcut. If a selected key is occupied, Sonora disables its own binding and leaves the existing one untouched. Removing the integration deletes only Sonora's managed configuration.

## Configuration

Edit config/settings.env to change the Whisper language, thread count, recording sample rate, or channel count.

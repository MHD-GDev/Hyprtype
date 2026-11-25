# Hyprtype

**Minimal, local voice dictation for Arch Linux and Hyprland.**

Hyprtype turns your speech into text and types the transcription into the currently focused application. It uses [Whisper.cpp](https://github.com/ggml-org/whisper.cpp) for on-device speech recognition and displays a compact recording indicator on your desktop.

- **Local speech recognition** — transcription runs on your machine.
- **Push-to-talk** — hold a configurable shortcut to record; release it to transcribe.
- **Types where you work** — inserts the result into the focused application.
- **Two interfaces** — choose the GTK interface or the optional Qt interface.
- **Native Hyprland integration** — configure and manage your shortcut from the command line.

> **Platform support:** Hyprtype is currently designed for Arch Linux, Wayland, and Hyprland. Other desktop environments and compositors are not supported.

## Requirements

The GTK interface requires:

- Arch Linux with Hyprland
- Python 3
- PipeWire and WirePlumber
- GTK 3 and GTK Layer Shell
- `wtype` and `libnotify`
- Git, CMake, a C/C++ toolchain, and `curl`

The optional Qt interface additionally requires PySide6, Qt 6, and Layer Shell support for Qt.

## Installation

### 1. Install system dependencies

On Arch Linux:

```bash
sudo pacman -S --needed git base-devel cmake curl python gtk3 gtk-layer-shell \
  python-gobject pipewire wireplumber libnotify wtype
```

For the optional Qt interface:

```bash
sudo pacman -S --needed pyside6 layer-shell-qt
```

### 2. Clone Hyprtype

Clone the repository with its Whisper.cpp submodule:

```bash
git clone --recurse-submodules https://github.com/MHD-GDev/Hyprtype.git
cd Hyprtype
```

If you already cloned the repository without its submodule, initialize it with:

```bash
git submodule update --init --recursive
```

### 3. Add the commands to your PATH

From the project directory:

```bash
mkdir -p ~/.local/bin
ln -sfn "$(realpath ./hyprtype)" ~/.local/bin/hyprtype
ln -sfn "$(realpath ./hyprtype-setup)" ~/.local/bin/hyprtype-setup
```

Make sure `~/.local/bin` is in your `PATH`.

### 4. Run the setup wizard

```bash
hyprtype-setup
```

The wizard asks you to choose the interface first (**Normal (GTK)** or **Qt**), then configure your push-to-talk shortcut. Right Alt is the default.

During setup, Hyprtype initializes the Whisper.cpp submodule if needed, builds the speech recognition engine, downloads the English `base.en` model, and verifies its SHA-1 checksum. This initial setup requires an internet connection and may take a few minutes. If required build tools are missing, the setup process offers to install them through `pacman`.

The Whisper.cpp executable and model are kept in `~/.local/share/hyprtype/`, outside the project checkout. Existing valid builds and models from older checkouts are migrated automatically.

When setup is complete, launch Hyprtype:

```bash
hyprtype
```

## Usage

1. Focus the application or text field where you want to enter text.
2. Press and hold your configured push-to-talk shortcut.
3. Speak naturally.
4. Release the shortcut. Hyprtype transcribes the recording and types the result into the focused application.

## Shortcut management

Use `hyprtype-setup` to open the interactive setup wizard.

| Command | Action |
| --- | --- |
| `hyprtype-setup --status` | Show the current integration status |
| `hyprtype-setup --dependencies` | Install or repair Whisper.cpp and its model |
| `hyprtype-setup --set "SUPER + SPACE"` | Set the push-to-talk shortcut |
| `hyprtype-setup --repair` | Reapply the saved shortcut |
| `hyprtype-setup --remove` | Remove Hyprtype's managed Hyprland integration |

Hyprtype supports both `~/.config/hypr/hyprland.lua` and `~/.config/hypr/hyprland.conf`. It backs up the Hyprland configuration before modifying it and only manages its own marked section.

If a selected shortcut conflicts with an existing binding, Hyprtype does not overwrite that binding. Instead, its own shortcut is disabled so the existing configuration remains intact.

## Configuration

The default settings are stored in `config/settings.env`:

```ini
WHISPER_THREADS=2
WHISPER_LANGUAGE=en
RECORD_RATE=16000
RECORD_CHANNELS=1
```

| Setting | Description |
| --- | --- |
| `WHISPER_THREADS` | Number of CPU threads used for transcription |
| `WHISPER_LANGUAGE` | Language code passed to Whisper |
| `RECORD_RATE` | Recording sample rate |
| `RECORD_CHANNELS` | Number of recording channels |

## Runtime files

Hyprtype keeps its speech-recognition engine and model under `$XDG_DATA_HOME/hyprtype/` (default: `~/.local/share/hyprtype/`).

Temporary recordings and runtime state are stored in `$XDG_RUNTIME_DIR/hyprtype-dictation/`. If `XDG_RUNTIME_DIR` is unset, Hyprtype uses a private per-user directory under `/tmp`. Runtime directories are checked for ownership and restrictive permissions before use.

## Troubleshooting

- **No transcription:** Run `hyprtype-setup --dependencies` to verify or repair the local Whisper.cpp engine and model.
- **Recording does not start:** Check that PipeWire and WirePlumber are running and that your microphone is available.
- **Text is not inserted:** Verify that `wtype` is installed and that the target application accepts Wayland virtual-keyboard input.
- **Shortcut does not work:** Run `hyprtype-setup --status`. If the shortcut is occupied, choose another one with the setup wizard or `--set`.
- **Qt interface does not launch:** Install the optional Qt dependencies. The GTK interface does not require them.

## Acknowledgements

- [Whisper.cpp](https://github.com/ggml-org/whisper.cpp) — local speech recognition.
- [Hyprland](https://hypr.land/) — the Wayland compositor Hyprtype is built for.

---

Hyprtype is an open-source project distributed under the [MIT License](LICENSE).

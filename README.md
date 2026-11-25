# Hyprtype

**Minimal, local voice dictation for Hyprland.**

Hyprtype turns speech into text and types the transcription directly into the focused application. It uses [Whisper.cpp](https://github.com/ggml-org/whisper.cpp) for on-device speech recognition and provides a compact recording orb for Wayland.

- **Local transcription** — audio is processed on your machine.
- **Push-to-talk** — hold a configurable shortcut to record; release it to transcribe.
- **Direct text input** — transcription is typed into the focused application.
- **Two interfaces** — choose the lightweight GTK interface or the optional Qt interface.
- **Hyprland integration** — configure and manage shortcuts from the command line.

## Requirements

Hyprtype currently targets **Arch Linux, Wayland, and Hyprland**.

Required: Python 3, PipeWire, WirePlumber, GTK 3, GTK Layer Shell, wtype, libnotify, Git, CMake, a C/C++ toolchain, and curl. Hyprtype automatically builds Whisper.cpp and downloads its default model during setup.

The optional Qt interface additionally requires PySide6, Qt 6, and Layer Shell support for Qt.

## Installation

### 1. Install dependencies

On Arch Linux:

```bash
sudo pacman -S --needed git base-devel cmake curl python gtk3 gtk-layer-shell \
  python-gobject pipewire wireplumber libnotify wtype
```

For the optional Qt interface:

```bash
sudo pacman -S --needed pyside6 layer-shell-qt
```

### 2. Clone the repository

Clone with the Whisper.cpp submodule:

```bash
git clone --recurse-submodules https://github.com/MHD-GDev/Hyprtype.git
cd Hyprtype
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

### 3. Add the commands to your PATH

Run from the project directory:

```bash
mkdir -p ~/.local/bin
ln -sfn "$(realpath ./hyprtype)" ~/.local/bin/hyprtype
ln -sfn "$(realpath ./hyprtype-setup)" ~/.local/bin/hyprtype-setup
```

Ensure ~/.local/bin is in your PATH. Then run the setup wizard:

```bash
hyprtype-setup
```

Setup automatically initializes the Whisper.cpp submodule if needed, builds the speech recognition engine, downloads the English base.en model, and verifies its SHA-1 checksum. The first setup can take a few minutes and requires an internet connection. If build tools are missing, Hyprtype offers to install them through pacman.

The engine and model are stored in ~/.local/share/hyprtype/, not in the project checkout. Existing valid Whisper builds and models from older checkouts are migrated automatically.

You can also install or repair only the speech dependencies with:

```bash
hyprtype-setup --dependencies
```

Then launch:

```bash
hyprtype
```

## First-time setup

On first launch, Hyprtype attempts to register **Right Alt** as the push-to-talk shortcut. If that key is already occupied, Hyprtype leaves the existing binding untouched and disables its own shortcut.

Run the interactive setup to select the interface and shortcut:

```bash
hyprtype-setup
```

The wizard asks you to choose the interface first (**Normal (GTK)** or **Qt**), then asks for your push-to-talk key. Your choices are saved and used by the main hyprtype command.

## Usage

1. Focus the application or text field where you want the text.
2. Hold your configured push-to-talk shortcut.
3. Speak.
4. Release the shortcut.

## Shortcut management

| Command | Action |
| --- | --- |
| hyprtype-setup | Open the interactive setup |
| hyprtype-setup --status | Show integration status |
| hyprtype-setup --dependencies | Install or repair Whisper.cpp and its model |
| hyprtype-setup --set "SUPER + SPACE" | Set a shortcut |
| hyprtype-setup --repair | Reapply the saved shortcut |
| hyprtype-setup --remove | Remove Hyprtype's managed Hyprland integration |

Hyprtype supports both ~/.config/hypr/hyprland.lua and ~/.config/hypr/hyprland.conf. It backs up the configuration before modifying it and only manages its own marked section. If a selected shortcut conflicts with an existing binding, Hyprtype does not replace that binding; its own shortcut is disabled instead.

## Configuration

Edit config/settings.env:

```ini
WHISPER_THREADS=2
WHISPER_LANGUAGE=en
RECORD_RATE=16000
RECORD_CHANNELS=1
```

| Setting | Description |
| --- | --- |
| WHISPER_THREADS | CPU threads used for transcription |
| WHISPER_LANGUAGE | Language code passed to Whisper |
| RECORD_RATE | Recording sample rate |
| RECORD_CHANNELS | Number of recording channels |

## Runtime files

The Whisper.cpp executable and model are stored under $XDG_DATA_HOME/hyprtype/ (default: ~/.local/share/hyprtype/). Temporary recordings and runtime state are stored in $XDG_RUNTIME_DIR/hyprtype-dictation/; if XDG_RUNTIME_DIR is unset, Hyprtype uses a private per-user directory under /tmp. Runtime directories are checked for ownership and restrictive permissions before use.

## Troubleshooting

- **No transcription:** run hyprtype-setup --dependencies to verify or repair the local Whisper.cpp engine and model.
- **Recording does not start:** check that PipeWire and WirePlumber are running and your microphone is available.
- **Text is not inserted:** verify that wtype is installed and the target application accepts Wayland virtual-keyboard input.
- **Shortcut does not work:** run hyprtype-setup --status; choose another key if the current one is occupied.
- **Qt does not launch:** install the optional Qt dependencies. The GTK interface does not need them.

## Acknowledgements

- [Whisper.cpp](https://github.com/ggml-org/whisper.cpp) for local speech recognition.
- [Hyprland](https://hypr.land/) for the Wayland compositor this project is built for.

---

Hyprtype is designed specifically for Hyprland. Other desktop environments and compositors are not currently supported.

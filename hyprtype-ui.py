#!/usr/bin/env python3
"""Hyprtype recording orb for Hyprland/Wayland."""
import os
import signal
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def secure_runtime_directory():
    """Create a private runtime directory, refusing unsafe pre-existing paths."""
    uid = os.getuid()
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        base = Path(xdg_runtime)
        try:
            base_info = base.lstat()
        except OSError as exc:
            raise RuntimeError(f"Invalid XDG_RUNTIME_DIR: {exc}") from exc
        if (not stat.S_ISDIR(base_info.st_mode) or base_info.st_uid != uid
                or base_info.st_mode & 0o077):
            raise RuntimeError("XDG_RUNTIME_DIR must be a private directory owned by the current user.")
        runtime = base / "hyprtype-dictation"
    else:
        # A per-user name avoids sharing a predictable directory in /tmp.
        runtime = Path("/tmp") / f"hyprtype-dictation-{uid}"

    try:
        runtime.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise RuntimeError(f"Refusing to use an unsafe runtime directory: {runtime}")
    if info.st_mode & 0o077:
        os.chmod(runtime, 0o700)
    return runtime


RUNTIME = secure_runtime_directory()
PIDFILE = RUNTIME / 'hyprtype.pid'
WAV = RUNTIME / 'dictation.wav'
SETTINGS = ROOT / 'config' / 'settings.env'
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
UI_FILE = CONFIG_HOME / 'hyprtype' / 'ui.json'


def selected_ui():
    try:
        import json
        value = json.loads(UI_FILE.read_text()).get('ui', 'normal')
        return value if value in ('normal', 'qt') else 'normal'
    except (OSError, ValueError, AttributeError):
        return 'normal'


def launch_selected_ui():
    mode = '--qt' if selected_ui() == 'qt' else '--daemon'
    command = [sys.executable, str(Path(__file__).resolve()), mode]
    with open(os.devnull, 'wb') as sink:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, start_new_session=True, close_fds=True)


def load_settings():
    values = {'WHISPER_THREADS': '2', 'WHISPER_LANGUAGE': 'en', 'RECORD_RATE': '16000', 'RECORD_CHANNELS': '1'}
    if SETTINGS.exists():
        for line in SETTINGS.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key in values:
                    values[key] = value.strip().strip('"\'')
    return values


CFG = load_settings()
from integration.dependencies import MODEL, WHISPER, ensure_dependencies, library_environment
from integration.microphone import normalize_recording


def active_pid():
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
        # A stale PID can be reused by an unrelated process. Only signal a
        # process whose command line identifies this exact Hyprtype script.
        command_line = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        expected_script = str(Path(__file__).resolve()).encode()
        if expected_script not in command_line:
            PIDFILE.unlink(missing_ok=True)
            return None
        return pid
    except (OSError, ValueError):
        PIDFILE.unlink(missing_ok=True)
        return None


def toggle():
    pid = active_pid()
    if pid:
        os.kill(pid, signal.SIGUSR1)
        return 0
    launch_selected_ui()
    return 0


def run_qt_fallback():
    """PySide6 orb rendered as a native Wayland layer-shell surface."""
    try:
        from PySide6 import QtCore, QtQml
        from PySide6.QtCore import Qt, QTimer, QObject, Signal, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except Exception as exc:
        print(f"Hyprtype Qt UI dependencies unavailable: {exc}", file=sys.stderr)
        return 1

    if not WHISPER.is_file() or not MODEL.is_file():
        print("Hyprtype cannot find its Whisper.cpp executable or model.", file=sys.stderr)
        return 1
    if os.environ.get("XDG_SESSION_TYPE") == "x11":
        print("hyprtype-qt requires a Wayland session for layer-shell positioning.", file=sys.stderr)
        return 1

    # The KDE integration turns the QML Window into a wlr-layer-shell surface.
    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.setApplicationName("Hyprtype")

    class Bridge(QObject):
        status = Signal(str)
        begin_close = Signal()
        closed = Signal()

    bridge = Bridge()
    state = {"finish": False, "cancel": False, "finished": False, "closing": False}
    focused_window = None
    try:
        import json
        active = subprocess.run(
            ["hyprctl", "-j", "activewindow"], capture_output=True,
            text=True, timeout=1, check=True
        )
        focused_window = json.loads(active.stdout or "{}").get("address") or None
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass

    try:
        WAV.unlink(missing_ok=True)
        recorder = subprocess.Popen([
            "pw-record", "--rate", CFG["RECORD_RATE"], "--channels", CFG["RECORD_CHANNELS"],
            "--format", "s16", "--container", "wav", str(WAV)
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("pw-record was not found. Install PipeWire.", file=sys.stderr)
        return 1

    PIDFILE.write_text(str(os.getpid()))
    try:
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "0"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass

    qml = r"""
import QtQuick
import QtQuick.Window as QQW
import org.kde.layershell 1.0 as LayerShell

QQW.Window {
    id: root
    visible: true
    width: Screen.width
    height: 108
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.Tool
    title: "Hyprtype"
    property int slideOffset: -116
    property bool closing: false

    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorBottom
                               | LayerShell.Window.AnchorLeft
                               | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.margins: ({ left: 0, top: 0, right: 0, bottom: 34 + slideOffset })
    LayerShell.Window.scope: "hyprtype-qt"

    Rectangle {
        id: orb
        width: 88
        height: 88
        radius: 44
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        color: "#19191b"
        border.width: 1
        border.color: "#414145"
        Text {
            id: statusLabel
            anchors.fill: parent
            anchors.margins: 5
            text: "LISTENING"
            color: "#a5a5aa"
            font.family: "Meslo LG M"
            font.pixelSize: 10
            font.weight: Font.Black
            font.letterSpacing: 1
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    NumberAnimation {
        id: entrance
        target: root
        property: "slideOffset"
        from: -116
        to: 0
        duration: 320
        easing.type: Easing.OutCubic
    }
    NumberAnimation {
        id: exit
        target: root
        property: "slideOffset"
        from: 0
        to: -116
        duration: 260
        easing.type: Easing.InCubic
        onFinished: bridge.closed()
    }
    Connections {
        target: bridge
        function onStatus(value) { statusLabel.text = value }
        function onBegin_close() { exit.start() }
    }
    Component.onCompleted: entrance.start()
}
"""
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.loadData(qml.encode("utf-8"))
    if not engine.rootObjects():
        stop = recorder
        stop.send_signal(signal.SIGINT)
        stop.wait(timeout=2)
        PIDFILE.unlink(missing_ok=True)
        print("Hyprtype could not initialize the Qt Wayland layer surface.", file=sys.stderr)
        return 1
    window = engine.rootObjects()[0]

    def stop_recorder():
        if recorder.poll() is None:
            recorder.send_signal(signal.SIGINT)
            try:
                recorder.wait(timeout=3)
            except subprocess.TimeoutExpired:
                recorder.terminate()
                try:
                    recorder.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    recorder.kill()

    def close_app(remove_audio=True):
        if state["closing"]:
            return
        state["closing"] = True
        stop_recorder()
        if remove_audio:
            WAV.unlink(missing_ok=True)
        bridge.begin_close.emit()

    @Slot()
    def finish_close():
        PIDFILE.unlink(missing_ok=True)
        app.quit()

    bridge.closed.connect(finish_close)

    def transcribe_worker():
        try:
            if not WAV.is_file() or WAV.stat().st_size < 1000:
                bridge.status.emit("NO AUDIO")
                import time
                time.sleep(0.7)
                close_app(True)
                return
            normalize_recording(WAV)
            env = library_environment()
            result = subprocess.run([
                "nice", "-n", "5", str(WHISPER), "-m", str(MODEL), "-f", str(WAV),
                "-l", CFG["WHISPER_LANGUAGE"], "-t", CFG["WHISPER_THREADS"],
                "-bo", "1", "-ng", "-nt", "-np"
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=env)
            if result.returncode != 0:
                (ROOT / "logs").mkdir(exist_ok=True)
                with (ROOT / "logs" / "hyprtype-errors.log").open("a") as log:
                    log.write("\n--- Whisper failed ---\n" + result.stderr + "\n")
                bridge.status.emit("WHISPER ERROR")
                subprocess.run(["notify-send", "Hyprtype", "Whisper failed. See logs/hyprtype-errors.log."],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                text = " ".join(result.stdout.split()).strip()
                if text:
                    bridge.status.emit("TYPING")
                    import time
                    time.sleep(0.18)
                    if focused_window:
                        subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{focused_window}"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, check=False)
                        time.sleep(0.12)
                    typed = subprocess.run(["wtype", "--", text], stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, check=False)
                    if typed.returncode == 0:
                        bridge.status.emit("COMPLETE")
                    else:
                        bridge.status.emit("INPUT ERROR")
                else:
                    bridge.status.emit("NO SPEECH")
                    subprocess.run(["notify-send", "Hyprtype", "No speech recognized"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            bridge.status.emit("ERROR")
            print(f"Hyprtype Qt fallback error: {exc}", file=sys.stderr)
        import time
        time.sleep(0.85)
        close_app(True)

    def check_signals():
        if state["cancel"] and not state["finished"]:
            state["finished"] = True
            bridge.status.emit("CANCELLED")
            subprocess.run(["notify-send", "-t", "1200", "Hyprtype", "Recording cancelled"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QTimer.singleShot(250, lambda: close_app(True))
            return
        if state["finish"] and not state["finished"]:
            state["finished"] = True
            stop_recorder()
            bridge.status.emit(".")
            dots = {"n": 0}
            def animate():
                dots["n"] = (dots["n"] % 4) + 1
                bridge.status.emit("." * dots["n"])
            dot_timer = QTimer()
            dot_timer.timeout.connect(animate)
            dot_timer.start(300)
            bridge.status.connect(lambda value: dot_timer.stop() if value != "." else None)
            threading.Thread(target=transcribe_worker, daemon=True).start()
            return
        if recorder.poll() is not None and not state["finished"]:
            state["finished"] = True
            bridge.status.emit("AUDIO STOPPED")
            QTimer.singleShot(700, lambda: close_app(True))

    def on_signal(signum, _frame):
        if signum == signal.SIGUSR1:
            state["finish"] = True
        else:
            state["cancel"] = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGUSR1, on_signal)

    poll = QTimer()
    poll.timeout.connect(check_signals)
    poll.start(60)
    app.aboutToQuit.connect(lambda: (stop_recorder(), PIDFILE.unlink(missing_ok=True)))
    return app.exec()



def ensure_first_launch_setup():
    """Register the default Hyprland shortcut once, without blocking app startup."""
    if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        return
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    state_file = config_home / 'hyprtype' / 'hyprland' / 'setup.json'
    if state_file.exists():
        return
    setup_command = ROOT / 'hyprtype-setup'
    if not setup_command.is_file():
        return
    try:
        subprocess.run(
            [str(setup_command), '--auto'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def prepare_dependencies():
    try:
        ensure_dependencies(interactive=sys.stdin.isatty())
        return True
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        message = f'Hyprtype setup required: {exc}'
        print(message, file=sys.stderr)
        if shutil.which('notify-send'):
            subprocess.run(['notify-send', '-a', 'Hyprtype', '-u', 'critical',
                            'Hyprtype setup required', str(exc)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return False


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else None
    if action in (None, '--qt', '--daemon'):
        if not prepare_dependencies():
            return 1
    if action in (None, '--qt'):
        ensure_first_launch_setup()
    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action == '--qt':
            return run_qt_fallback()
        if action == '--toggle':
            return toggle()
        if action == '--start':
            if not active_pid():
                launch_selected_ui()
            return 0
        if action == '--finish':
            pid = active_pid()
            if pid:
                os.kill(pid, signal.SIGUSR1)
            return 0
        if action == '--cancel':
            pid = active_pid()
            if pid:
                os.kill(pid, signal.SIGINT)
            return 0
        if action not in ('--daemon',):
            print('Usage: hyprtype [--start|--finish|--toggle|--cancel]', file=sys.stderr)
            return 2
        daemon = action == '--daemon'
    else:
        daemon = False

    if not daemon and selected_ui() == 'qt':
        return run_qt_fallback()

    existing = active_pid()
    if existing:
        print(f'Hyprtype is already running (PID {existing}). Use `hyprtype --toggle` to finish it.', file=sys.stderr)
        return 1

    try:
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('GtkLayerShell', '0.1')
        from gi.repository import Gtk, Gdk, GLib, GtkLayerShell
    except Exception as exc:
        print(f'Hyprtype GTK UI dependencies unavailable: {exc}', file=sys.stderr)
        return 1

    if not WHISPER.is_file() or not MODEL.is_file():
        print('Hyprtype cannot find its Whisper.cpp executable or model.', file=sys.stderr)
        return 1

    # Remember the window that was focused when dictation began. The orb is
    # non-focusable, and we restore this target before typing so the overlay
    # can never become the destination for the transcription.
    focused_window = None
    try:
        active = subprocess.run(
            ['hyprctl', '-j', 'activewindow'], capture_output=True,
            text=True, timeout=1, check=True
        )
        import json
        active_data = json.loads(active.stdout or '{}')
        focused_window = active_data.get('address') or None
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass

    cancel_requested = False
    finish_requested = False
    recorder = None
    finished = False

    def on_signal(signum, _frame):
        nonlocal cancel_requested, finish_requested
        if signum == signal.SIGUSR1:
            finish_requested = True
        else:
            cancel_requested = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGUSR1, on_signal)

    try:
        WAV.unlink(missing_ok=True)
        recorder = subprocess.Popen([
            'pw-record', '--rate', CFG['RECORD_RATE'], '--channels', CFG['RECORD_CHANNELS'],
            '--format', 's16', '--container', 'wav', str(WAV)
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print('pw-record was not found. Install PipeWire.', file=sys.stderr)
        return 1

    PIDFILE.write_text(str(os.getpid()))
    try:
        subprocess.run(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SOURCE@', '0'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass

    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title('Hyprtype')
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_app_paintable(True)
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        window.set_visual(visual)

    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    # Stretch horizontally, but anchor only to the bottom so the orb sits
    # centred along the lower edge rather than in the middle of the display.
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, False)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
    # Start just below the screen; the orb slides into its resting position.
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, -110)
    GtkLayerShell.set_exclusive_zone(window, -1)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
    try:
        monitor = Gdk.Display.get_default().get_primary_monitor()
        if monitor:
            GtkLayerShell.set_monitor(window, monitor)
    except Exception:
        pass

    css = Gtk.CssProvider()
    css.load_from_data(b'''
        window { background: transparent; box-shadow: none; border: none; }
        .hyprtype-orb {
            min-width: 88px; min-height: 88px;
            background: #19191b;
            border: 1px solid #414145;
            border-radius: 100px;
            box-shadow: none;
            padding: 0px;
        }
        .hyprtype-status { color: #a5a5aa; font-family: 'Meslo LG M'; font-size: 10px; font-weight: 900; letter-spacing: 1.2px; }
    ''')
    Gtk.StyleContext.add_provider_for_screen(screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    orb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    orb.set_halign(Gtk.Align.CENTER)
    orb.set_valign(Gtk.Align.CENTER)
    orb.get_style_context().add_class('hyprtype-orb')
    status = Gtk.Label(label='LISTENING')
    status.get_style_context().add_class('hyprtype-status')
    status.set_halign(Gtk.Align.CENTER)
    status.set_valign(Gtk.Align.CENTER)
    orb.pack_start(status, True, True, 0)
    window.add(orb)

    def stop_recorder():
        nonlocal recorder
        if recorder and recorder.poll() is None:
            recorder.send_signal(signal.SIGINT)
            try:
                recorder.wait(timeout=3)
            except subprocess.TimeoutExpired:
                recorder.terminate()
                try:
                    recorder.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    recorder.kill()
        recorder = None

    closing = False

    def close_app(remove_audio=True):
        nonlocal closing
        if closing:
            return
        closing = True
        stop_recorder()
        import time
        slide_started = time.monotonic()
        slide_duration = 0.26

        def slide_orb_down():
            progress = min((time.monotonic() - slide_started) / slide_duration, 1.0)
            eased = progress * progress * progress
            margin = round(34 - (144 * eased))
            GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, margin)
            if progress < 1.0:
                return True
            if remove_audio:
                WAV.unlink(missing_ok=True)
            PIDFILE.unlink(missing_ok=True)
            Gtk.main_quit()
            return False

        GLib.timeout_add(16, slide_orb_down)

    def update_status(value):
        status.set_text(value)

    animation_source = None
    animation_frame = 0

    def animate_processing():
        nonlocal animation_frame
        animation_frame = (animation_frame + 1) % 4
        status.set_text('.' * animation_frame if animation_frame else '.')
        return True

    def start_processing_animation():
        nonlocal animation_source, animation_frame
        animation_frame = 0
        status.set_text('.')
        if animation_source is not None:
            GLib.source_remove(animation_source)
        animation_source = GLib.timeout_add(300, animate_processing)

    def stop_processing_animation():
        nonlocal animation_source
        if animation_source is not None:
            GLib.source_remove(animation_source)
            animation_source = None

    def transcribe_worker():
        try:
            if not WAV.is_file() or WAV.stat().st_size < 1000:
                GLib.idle_add(update_status, 'NO AUDIO')
                GLib.timeout_add(700, lambda: (close_app(True), False)[1])
                return
            normalize_recording(WAV)
            whisper_env = library_environment()
            result = subprocess.run([
                'nice', '-n', '5', str(WHISPER), '-m', str(MODEL), '-f', str(WAV),
                '-l', CFG['WHISPER_LANGUAGE'], '-t', CFG['WHISPER_THREADS'],
                '-bo', '1', '-ng', '-nt', '-np'
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=whisper_env)
            if result.returncode != 0:
                (ROOT / 'logs').mkdir(exist_ok=True)
                with (ROOT / 'logs' / 'hyprtype-errors.log').open('a') as log:
                    log.write('\\n--- Whisper failed ---\\n' + result.stderr + '\\n')
                GLib.idle_add(update_status, 'WHISPER ERROR')
                subprocess.run(['notify-send', 'Hyprtype', 'Whisper failed. See logs/hyprtype-errors.log.'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                GLib.timeout_add(1200, lambda: (close_app(True), False)[1])
                return
            text = ' '.join(result.stdout.split()).strip()
            if text:
                # Show the final stage explicitly before sending keystrokes.
                # Let GTK process the update so TYPING is actually visible.
                import time
                GLib.idle_add(stop_processing_animation)
                GLib.idle_add(update_status, 'TYPING')
                time.sleep(0.18)
                # Return focus to the application that was active when Hyprtype
                # started, then inject text at that application's text cursor.
                if focused_window:
                    subprocess.run(
                        ['hyprctl', 'dispatch', 'focuswindow', f'address:{focused_window}'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=2, check=False
                    )
                    time.sleep(0.12)
                typed = subprocess.run(['wtype', '--', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if typed.returncode == 0:
                    GLib.idle_add(update_status, 'COMPLETE')
                else:
                    GLib.idle_add(update_status, 'INPUT ERROR')
            else:
                GLib.idle_add(update_status, 'NO SPEECH')
                subprocess.run(['notify-send', 'Hyprtype', 'No speech recognized'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            GLib.idle_add(update_status, 'ERROR')
        GLib.timeout_add(850, lambda: (close_app(True), False)[1])

    def check_signals():
        nonlocal finished
        if cancel_requested and not finished:
            finished = True
            update_status('CANCELLED')
            subprocess.run(['notify-send', '-t', '1200', 'Hyprtype', 'Recording cancelled'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            GLib.timeout_add(250, lambda: (close_app(True), False)[1])
            return False
        if finish_requested and not finished:
            finished = True
            start_processing_animation()
            stop_recorder()
            threading.Thread(target=transcribe_worker, daemon=True).start()
            return False
        if recorder and recorder.poll() is not None and not finished:
            finished = True
            update_status('AUDIO STOPPED')
            GLib.timeout_add(700, lambda: (close_app(True), False)[1])
            return False
        return True

    window.connect('destroy', lambda *_: close_app(True) if not finished else None)
    window.show_all()
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)

    # Ease the bottom margin from below the screen to the normal position.
    import time
    slide_started = time.monotonic()
    slide_duration = 0.32

    def slide_orb_up():
        progress = min((time.monotonic() - slide_started) / slide_duration, 1.0)
        eased = 1.0 - (1.0 - progress) ** 3
        margin = round(-110 + (144 * eased))
        GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, margin)
        return progress < 1.0

    GLib.timeout_add(16, slide_orb_up)
    GLib.timeout_add(60, check_signals)
    Gtk.main()
    stop_recorder()
    PIDFILE.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

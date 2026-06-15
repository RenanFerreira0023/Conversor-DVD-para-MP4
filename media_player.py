from __future__ import annotations

import os
import platform
import re
import logging
import tkinter as tk
import shutil
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Media Player VHS/DVD"
SUPPORTED_FILES = (
    "*.vob",
    "*.mpg",
    "*.mpeg",
    "*.mp4",
    "*.avi",
    "*.mkv",
    "*.mov",
    "*.wmv",
)


logger = logging.getLogger(APP_TITLE)
logger.setLevel(logging.INFO)

log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

VLC_SEARCH_PATHS: list[Path] = []
COMMON_FFMPEG_BIN = Path(r"C:\ffmpeg\bin")


def find_media_tool(name: str) -> str | None:
    tool = shutil.which(name)
    if tool:
        return tool

    suffix = ".exe" if platform.system() == "Windows" else ""
    fallback = COMMON_FFMPEG_BIN / f"{name}{suffix}"
    if fallback.exists():
        return str(fallback)

    return None


def add_vlc_dll_paths() -> None:
    """Make python-vlc find libvlc.dll on common Windows installs."""
    if platform.system() != "Windows":
        return

    candidates: list[Path] = [
        Path(os.environ["VLC_PATH"]) if os.environ.get("VLC_PATH") else Path(),
        Path(os.environ["VLC_PLUGIN_PATH"]).parent if os.environ.get("VLC_PLUGIN_PATH") else Path(),
        Path(r"C:\Program Files\VideoLAN\VLC"),
        Path(r"C:\Program Files (x86)\VideoLAN\VLC"),
    ]

    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_name in (
                r"SOFTWARE\VideoLAN\VLC",
                r"SOFTWARE\WOW6432Node\VideoLAN\VLC",
            ):
                try:
                    with winreg.OpenKey(root, key_name) as key:
                        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                        candidates.append(Path(install_dir))
                except OSError:
                    pass
    except Exception:
        logger.debug("Nao foi possivel consultar o Registro do Windows.", exc_info=True)

    VLC_SEARCH_PATHS[:] = [path for path in candidates if str(path)]

    for path in candidates:
        if path and (path / "libvlc.dll").exists():
            os.environ.setdefault("VLC_PLUGIN_PATH", str(path / "plugins"))
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(path))
            logger.info("libvlc.dll encontrado em: %s", path)
            return


add_vlc_dll_paths()

try:
    import vlc  # type: ignore
except Exception as exc:  # pragma: no cover - depends on local install
    vlc = None
    VLC_IMPORT_ERROR = exc
    logger.exception("Falha ao importar python-vlc ou carregar libvlc.")
else:
    VLC_IMPORT_ERROR = None


class MediaPlayerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x760")
        self.minsize(760, 480)

        self.instance = None
        self.player = None
        self.event_manager = None
        self.playlist: list[Path] = []
        self.current_index = -1
        self.user_dragging = False
        self.is_stopped = True
        self.detected_duration_ms: int | None = None
        self.duration_source = ""
        self.duration_is_reliable = True
        self.can_seek = True
        self.playback_started_at: float | None = None
        self.elapsed_before_start_ms = 0
        self.duration_cache: dict[Path, tuple[int | None, str, bool, bool]] = {}
        self.seek_disabled_notice_shown = False

        self._build_ui()
        self._maximize_window()
        self._log("Aplicativo iniciado.", "info")

        if vlc is None:
            self._show_vlc_help()
        else:
            try:
                self._init_vlc()
            except Exception:
                self._log("Erro ao iniciar o VLC. Veja detalhes no arquivo de log.", "error", exc_info=True)
                self._show_vlc_help()
                return

        self.after(300, self._tick)

    def _build_ui(self) -> None:
        self._configure_style()
        self.configure(bg="#070a12")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(3, weight=0)

        toolbar = ttk.Frame(self, padding=(14, 10), style="App.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(4, weight=1)

        ttk.Button(toolbar, text="⏏ Arquivo", command=self.open_file, style="Player.TButton").grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="▣ DVD", command=self.open_folder, style="Player.TButton").grid(row=0, column=1, padx=8)
        ttk.Button(toolbar, text="⏮", command=self.previous_media, style="Icon.TButton").grid(row=0, column=2, padx=8)
        ttk.Button(toolbar, text="⏭", command=self.next_media, style="Icon.TButton").grid(row=0, column=3, padx=8)

        self.now_playing = tk.StringVar(value="Nenhum video carregado")
        ttk.Label(toolbar, textvariable=self.now_playing, anchor="e", style="Title.TLabel").grid(
            row=0, column=4, sticky="ew", padx=(16, 0)
        )

        self.video_frame = tk.Frame(self, bg="#000000", highlightthickness=1, highlightbackground="#1f6feb")
        self.video_frame.grid(row=1, column=0, sticky="nsew")

        controls = ttk.Frame(self, padding=(14, 12), style="Controls.TFrame")
        controls.grid(row=2, column=0, sticky="ew")
        controls.columnconfigure(5, weight=1)

        self.play_pause_text = tk.StringVar(value="▶")
        ttk.Button(controls, textvariable=self.play_pause_text, command=self.play_pause, style="Primary.Icon.TButton").grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(controls, text="■", command=self.stop, style="Icon.TButton").grid(row=0, column=1, padx=8)

        self.back_5_button = ttk.Button(controls, text="↶ 5s", command=lambda: self.skip_seconds(-5), style="Player.TButton")
        self.back_5_button.grid(row=0, column=2, padx=6)

        self.forward_5_button = ttk.Button(controls, text="5s ↷", command=lambda: self.skip_seconds(5), style="Player.TButton")
        self.forward_5_button.grid(row=0, column=3, padx=6)

        self.time_text = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(controls, textvariable=self.time_text, width=18, style="Time.TLabel").grid(row=0, column=4, padx=10)

        self.position = tk.DoubleVar(value=0)
        self.seek = ttk.Scale(
            controls,
            variable=self.position,
            from_=0,
            to=1000,
            command=self._on_seek_drag,
        )
        self.seek.grid(row=0, column=5, sticky="ew", padx=8)
        self.seek.bind("<ButtonPress-1>", self._start_seek)
        self.seek.bind("<ButtonRelease-1>", self._finish_seek)

        self.seek_note = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self.seek_note, width=28, style="Muted.TLabel").grid(row=1, column=5, sticky="w", padx=8)

        ttk.Label(controls, text="VOL", style="Muted.TLabel").grid(row=0, column=6, padx=(10, 4))
        self.volume = tk.IntVar(value=85)
        volume = ttk.Scale(controls, variable=self.volume, from_=0, to=100, command=self._set_volume)
        volume.grid(row=0, column=7, sticky="ew")

        self.status = tk.StringVar(value="Abra um arquivo .VOB/.MPG ou uma pasta VIDEO_TS.")
        status_bar = ttk.Label(self, textvariable=self.status, padding=(14, 7), anchor="w", style="Status.TLabel")
        status_bar.grid(row=3, column=0, sticky="ew")
        self._set_seek_enabled(False)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background="#070a12")
        style.configure("Controls.TFrame", background="#0c1220")
        style.configure("Title.TLabel", background="#070a12", foreground="#d7e7ff", font=("Segoe UI", 11, "bold"))
        style.configure("Time.TLabel", background="#0c1220", foreground="#7dd3fc", font=("Consolas", 12, "bold"))
        style.configure("Muted.TLabel", background="#0c1220", foreground="#8ea3bc", font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#070a12", foreground="#8ea3bc", font=("Segoe UI", 9))
        style.configure(
            "Player.TButton",
            background="#142033",
            foreground="#d7e7ff",
            borderwidth=1,
            focusthickness=0,
            padding=(12, 7),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Player.TButton",
            background=[("active", "#1f6feb"), ("disabled", "#141923")],
            foreground=[("disabled", "#526071")],
        )
        style.configure(
            "Icon.TButton",
            background="#142033",
            foreground="#d7e7ff",
            borderwidth=1,
            padding=(12, 7),
            font=("Segoe UI Symbol", 12, "bold"),
        )
        style.map("Icon.TButton", background=[("active", "#1f6feb"), ("disabled", "#141923")])
        style.configure(
            "Primary.Icon.TButton",
            background="#1f6feb",
            foreground="#ffffff",
            borderwidth=1,
            padding=(16, 8),
            font=("Segoe UI Symbol", 13, "bold"),
        )
        style.map("Primary.Icon.TButton", background=[("active", "#38bdf8"), ("disabled", "#141923")])
        style.configure("Horizontal.TScale", background="#0c1220", troughcolor="#142033")

    def _maximize_window(self) -> None:
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                width = self.winfo_screenwidth()
                height = self.winfo_screenheight()
                self.geometry(f"{width}x{height}+0+0")

    def _init_vlc(self) -> None:
        self.instance = vlc.Instance("--no-video-title-show", "--quiet")
        self.player = self.instance.media_player_new()
        self.player.audio_set_volume(self.volume.get())

        self.event_manager = self.player.event_manager()
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_end_reached)
        self.event_manager.event_attach(vlc.EventType.MediaPlayerEncounteredError, self._on_player_error)

        self.update_idletasks()
        handle = self.video_frame.winfo_id()
        system = platform.system()
        if system == "Windows":
            self.player.set_hwnd(handle)
        elif system == "Linux":
            self.player.set_xwindow(handle)
        elif system == "Darwin":
            self.player.set_nsobject(handle)

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Abrir filmagem",
            filetypes=[
                ("Videos antigos e comuns", " ".join(SUPPORTED_FILES)),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if path:
            self.load_path(Path(path))

    def open_folder(self) -> None:
        path = filedialog.askdirectory(title="Abrir pasta do DVD ou VIDEO_TS")
        if path:
            self.load_path(Path(path))

    def load_path(self, path: Path) -> None:
        if vlc is None:
            self._show_vlc_help()
            return

        try:
            path = path.resolve()
        except Exception:
            self._log(f"Erro ao resolver caminho: {path}", "error", exc_info=True)
            messagebox.showerror("Erro ao abrir", f"Nao consegui abrir este caminho:\n{path}")
            return

        self._log(f"Tentando abrir: {path}", "info")
        if path.is_dir():
            files, warning = self._videos_from_folder(path)
            if warning:
                self._log(warning, "warning")
                self.status.set(warning)
                messagebox.showwarning("DVD incompleto", warning)
            if not files:
                return
            self.playlist = files
            self.current_index = 0
            self._play_current()
            return

        if not path.exists():
            self._log(f"Arquivo nao encontrado: {path}", "error")
            messagebox.showerror("Arquivo nao encontrado", f"Nao encontrei:\n{path}")
            return

        self.playlist = [path]
        self.current_index = 0
        self._play_current()

    def _videos_from_folder(self, folder: Path) -> tuple[list[Path], str | None]:
        video_ts = folder if folder.name.upper() == "VIDEO_TS" else folder / "VIDEO_TS"
        search_root = video_ts if video_ts.exists() else folder

        vobs = sorted(search_root.glob("*.VOB")) + sorted(search_root.glob("*.vob"))
        movie_vobs = [path for path in vobs if not re.search(r"_0\.vob$", path.name, re.IGNORECASE)]
        files = movie_vobs or vobs

        if not files:
            sidecar_files = list(search_root.glob("*.IFO")) + list(search_root.glob("*.BUP"))
            if sidecar_files:
                return [], (
                    "Esta pasta tem arquivos .IFO/.BUP, mas nao tem arquivos .VOB. "
                    "Os .VOB sao os arquivos grandes que guardam o video e o audio do DVD. "
                    "Copie o DVD novamente incluindo todos os arquivos da pasta VIDEO_TS."
                )

            found: list[Path] = []
            for pattern in SUPPORTED_FILES:
                found.extend(folder.rglob(pattern))
            files = sorted(set(found))

        if not files:
            return [], "Nao encontrei videos nesta pasta. Procure arquivos .VOB, .MPG ou .MPEG."

        return self._sort_dvd_segments(files), None

    def _sort_dvd_segments(self, files: list[Path]) -> list[Path]:
        def key(path: Path) -> tuple[int, int, str]:
            match = re.search(r"VTS_(\d+)_(\d+)\.VOB$", path.name, re.IGNORECASE)
            if not match:
                return (999, 999, path.name.lower())
            return (int(match.group(1)), int(match.group(2)), path.name.lower())

        return sorted(files, key=key)

    def _play_current(self) -> None:
        if self.player is None or self.instance is None:
            self._log("Player VLC nao esta inicializado.", "error")
            return
        if not (0 <= self.current_index < len(self.playlist)):
            self._log(f"Indice invalido da playlist: {self.current_index}", "error")
            return

        path = self.playlist[self.current_index]
        self._load_duration(path)
        self.playback_started_at = time.monotonic()
        self.elapsed_before_start_ms = 0
        self.seek_disabled_notice_shown = False
        try:
            media = self.instance.media_new_path(str(path))
            self.player.set_media(media)
            result = self.player.play()
        except Exception:
            self._log(f"Erro ao tentar tocar: {path}", "error", exc_info=True)
            messagebox.showerror("Erro ao tocar", f"Nao consegui tocar:\n{path}\n\nVeja o log para detalhes.")
            return

        if result == -1:
            self._log(f"O VLC recusou o arquivo: {path}", "error")
            messagebox.showerror("Erro ao tocar", f"O VLC recusou este arquivo:\n{path}\n\nVeja o log para detalhes.")
            return

        self.is_stopped = False
        self.play_pause_text.set("⏸")
        self.now_playing.set(path.name)
        self.status.set(f"Tocando {self.current_index + 1}/{len(self.playlist)}: {path}")
        self._log(f"Tocando {self.current_index + 1}/{len(self.playlist)}: {path}", "info")
        if self.detected_duration_ms and self.duration_is_reliable:
            self._log(
                f"Duracao detectada por {self.duration_source}: {self._format_time(self.detected_duration_ms)}",
                "info",
            )
        elif self.detected_duration_ms:
            self._log(
                f"Duracao suspeita detectada ({self._format_time(self.detected_duration_ms)}). "
                "Vou mostrar tempo decorrido sem total.",
                "warning",
            )
        else:
            self._log("Duracao exata nao detectada. Vou mostrar o tempo decorrido sem confiar no total.", "warning")
        self._set_seek_enabled(self.can_seek)
        self._set_volume(str(self.volume.get()))

    def play_pause(self) -> None:
        if self.player is None:
            self._show_vlc_help()
            return
        if not self.playlist:
            self.open_file()
            return
        if self.is_stopped:
            self._play_current()
            return
        was_playing = bool(self.player.is_playing())
        self.player.pause()
        if was_playing and self.playback_started_at is not None:
            self.elapsed_before_start_ms += int((time.monotonic() - self.playback_started_at) * 1000)
            self.playback_started_at = None
        elif not was_playing:
            self.playback_started_at = time.monotonic()
        is_playing = bool(self.player.is_playing())
        self.play_pause_text.set("⏸" if is_playing else "▶")

    def stop(self) -> None:
        if self.player is None:
            return
        self.player.stop()
        self.is_stopped = True
        self.play_pause_text.set("▶")
        self.position.set(0)
        self.time_text.set("00:00 / 00:00")
        self.detected_duration_ms = None
        self.duration_source = ""
        self.duration_is_reliable = True
        self.can_seek = True
        self.playback_started_at = None
        self.elapsed_before_start_ms = 0
        self.seek_disabled_notice_shown = False
        self._set_seek_enabled(True)

    def previous_media(self) -> None:
        if not self.playlist:
            return
        self.current_index = max(0, self.current_index - 1)
        self._play_current()

    def next_media(self) -> None:
        if not self.playlist:
            return
        if self.current_index + 1 >= len(self.playlist):
            self.stop()
            self.status.set("Fim da lista.")
            return
        self.current_index += 1
        self._play_current()

    def skip_seconds(self, seconds: int) -> None:
        if self.player is None or not self.can_seek:
            return

        current = max(self.player.get_time(), 0)
        length = self._duration_for_controls()
        target = current + (seconds * 1000)
        if length > 0:
            target = min(target, max(length - 500, 0))
        target = max(target, 0)

        try:
            self.player.set_time(target)
            self.playback_started_at = time.monotonic()
            self.elapsed_before_start_ms = target
            self._log(f"Pulando para {self._format_time(target)}.", "info")
        except Exception:
            self._log("Erro ao pular no video.", "error", exc_info=True)

    def _on_end_reached(self, _event: object) -> None:
        if not self.duration_is_reliable:
            elapsed = self._wall_elapsed_ms()
            minimum_play_time = max((self.detected_duration_ms or 0) * 2, 60_000)
            if elapsed < minimum_play_time:
                self.after(
                    0,
                    lambda: self._log(
                        "O VLC disparou fim do arquivo cedo demais; mantendo o video atual.",
                        "warning",
                    ),
                )
                return
        self.after(0, self.next_media)

    def _on_player_error(self, _event: object) -> None:
        current = self.playlist[self.current_index] if 0 <= self.current_index < len(self.playlist) else None
        message = f"Erro do VLC ao reproduzir: {current}" if current else "Erro do VLC ao reproduzir a midia."
        self.after(0, lambda: self._log(message, "error"))
        self.after(0, lambda: self.status.set("Erro ao reproduzir. Veja o log."))

    def _start_seek(self, _event: tk.Event) -> None:
        if not self.can_seek:
            self.user_dragging = False
            return
        self.user_dragging = True

    def _finish_seek(self, _event: tk.Event) -> None:
        if not self.can_seek:
            self.user_dragging = False
            return
        self.user_dragging = False
        self._seek_to_current_value()

    def _on_seek_drag(self, _value: str) -> None:
        if self.user_dragging:
            return

    def _seek_to_current_value(self) -> None:
        if self.player is None:
            return
        if not self.can_seek:
            return
        length = self._duration_for_controls()
        if length <= 0:
            return
        try:
            self.player.set_time(int(length * (self.position.get() / 1000)))
            self.playback_started_at = time.monotonic()
            self.elapsed_before_start_ms = int(length * (self.position.get() / 1000))
        except Exception:
            self._log("Erro ao mudar a posicao do video.", "error", exc_info=True)

    def _set_volume(self, value: str) -> None:
        if self.player is not None:
            try:
                self.player.audio_set_volume(int(float(value)))
            except Exception:
                self._log("Erro ao ajustar o volume.", "error", exc_info=True)

    def _tick(self) -> None:
        if self.player is not None and not self.user_dragging:
            length = self._duration_for_controls()
            current = self._current_time_for_display(length)
            if current >= 0:
                if length > 0:
                    position = min((current / length) * 1000, 1000)
                    self.position.set(position)
                    self.time_text.set(f"{self._format_time(current)} / {self._format_time(length)}")
                else:
                    self.time_text.set(f"{self._format_time(current)} / --:--")

            if self.playlist and not self.is_stopped:
                self.play_pause_text.set("⏸" if self.player.is_playing() else "▶")

        self.after(300, self._tick)

    def _duration_for_controls(self) -> int:
        if self.detected_duration_ms and self.duration_is_reliable:
            return self.detected_duration_ms
        if self.player is None:
            return 0

        length = self.player.get_length()
        current = self.player.get_time()
        if length <= 0:
            return 0
        if current > length + 1500:
            if self.duration_source != "desconhecida":
                self.duration_source = "desconhecida"
                self._log(
                    f"O VLC informou uma duracao suspeita ({self._format_time(length)}). "
                    "Vou ocultar o total para nao mostrar um tempo falso.",
                    "warning",
                )
            return 0
        return length

    def _current_time_for_display(self, length: int) -> int:
        if self.player is None:
            return 0

        current = self.player.get_time()
        if length > 0 and self.duration_is_reliable and self.can_seek:
            return max(current, 0)

        return max(current, self._wall_elapsed_ms(), 0)

    def _wall_elapsed_ms(self) -> int:
        wall_elapsed = self.elapsed_before_start_ms
        if self.playback_started_at is not None and not self.is_stopped:
            wall_elapsed += int((time.monotonic() - self.playback_started_at) * 1000)
        return wall_elapsed

    def _is_duration_reliable(self, path: Path, duration_ms: int | None) -> bool:
        if duration_ms is None:
            return False

        old_dvd_extensions = {".vob", ".mpg", ".mpeg"}
        if path.suffix.lower() in old_dvd_extensions and duration_ms < 60_000:
            return False

        return True

    def _probe_duration(self, path: Path) -> tuple[int | None, str]:
        ffprobe = find_media_tool("ffprobe")
        if ffprobe:
            try:
                completed = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                raw_duration = completed.stdout.strip()
                if completed.returncode == 0 and raw_duration:
                    duration_seconds = float(raw_duration)
                    if duration_seconds > 0:
                        return int(duration_seconds * 1000), "ffprobe"
                if completed.stderr.strip():
                    self._log(f"ffprobe nao conseguiu ler a duracao: {completed.stderr.strip()}", "warning")
            except Exception:
                self._log("Erro ao consultar duracao com ffprobe.", "warning", exc_info=True)

        return None, ""

    def _load_duration(self, path: Path) -> None:
        cached = self.duration_cache.get(path)
        if cached:
            self.detected_duration_ms, self.duration_source, self.duration_is_reliable, self.can_seek = cached
            return

        duration_ms, source = self._probe_duration(path)
        is_reliable = self._is_duration_reliable(path, duration_ms)
        can_seek = is_reliable

        self.detected_duration_ms = duration_ms
        self.duration_source = source
        self.duration_is_reliable = is_reliable
        self.can_seek = can_seek
        self.duration_cache[path] = (duration_ms, source, is_reliable, can_seek)

        if not is_reliable and path.suffix.lower() in {".vob", ".mpg", ".mpeg"}:
            self._start_frame_count_duration_probe(path)

    def _start_frame_count_duration_probe(self, path: Path) -> None:
        def worker() -> None:
            duration_ms = self._probe_duration_by_counting_frames(path)
            if duration_ms is None:
                return
            self.duration_cache[path] = (duration_ms, "contagem de quadros", True, False)
            self.after(0, lambda: self._apply_counted_duration(path, duration_ms))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _apply_counted_duration(self, path: Path, duration_ms: int) -> None:
        current = self.playlist[self.current_index] if 0 <= self.current_index < len(self.playlist) else None
        if current != path:
            return

        self.detected_duration_ms = duration_ms
        self.duration_source = "contagem de quadros"
        self.duration_is_reliable = True
        self.can_seek = False
        self._set_seek_enabled(False)
        self._log(
            f"Duracao recuperada por contagem de quadros: {self._format_time(duration_ms)}. "
            "A barra de arrastar fica desativada para evitar travamento neste VOB.",
            "info",
        )

    def _probe_duration_by_counting_frames(self, path: Path) -> int | None:
        ffprobe = find_media_tool("ffprobe")
        if not ffprobe:
            return None

        self._log(f"Calculando duracao real por contagem de quadros: {path.name}", "info")
        try:
            completed = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-count_frames",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=nb_read_frames,avg_frame_rate",
                    "-of",
                    "default=noprint_wrappers=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except Exception:
            self._log("Erro ao contar quadros com ffprobe.", "warning", exc_info=True)
            return None

        if completed.returncode != 0:
            self._log(f"ffprobe falhou ao contar quadros: {completed.stderr.strip()}", "warning")
            return None

        values: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

        frame_count = values.get("nb_read_frames")
        frame_rate = values.get("avg_frame_rate")
        if not frame_count or not frame_rate or frame_count == "N/A" or frame_rate == "0/0":
            return None

        try:
            seconds = int(frame_count) / float(Fraction(frame_rate))
        except Exception:
            self._log("Nao consegui converter a contagem de quadros em duracao.", "warning", exc_info=True)
            return None

        if seconds <= 0:
            return None
        return int(seconds * 1000)

    def _set_seek_enabled(self, enabled: bool) -> None:
        if enabled:
            self.seek.state(["!disabled"])
            self.back_5_button.state(["!disabled"])
            self.forward_5_button.state(["!disabled"])
            self.seek_note.set("")
            return
        self.seek.state(["disabled"])
        self.back_5_button.state(["disabled"])
        self.forward_5_button.state(["disabled"])
        self.seek_note.set("Calculando tempo; busca desativada")

    def _format_time(self, milliseconds: int) -> str:
        if milliseconds < 0:
            milliseconds = 0
        seconds = milliseconds // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _show_vlc_help(self) -> None:
        searched_paths = "\n".join(f"- {path}" for path in VLC_SEARCH_PATHS) or "- nenhum caminho detectado"
        details = f"\n\nErro tecnico: {VLC_IMPORT_ERROR}" if VLC_IMPORT_ERROR else ""
        message = (
            "Este player usa o motor do VLC para conseguir tocar videos antigos de DVD/VHS.\n\n"
            "Instale estes dois itens:\n"
            "1. VLC Media Player para Windows, pelo site videolan.org\n"
            "2. A dependencia Python: pip install -r requirements.txt\n\n"
            "Caminhos procurados para libvlc.dll:\n"
            f"{searched_paths}"
            f"{details}"
        )
        self.status.set("Instale o VLC e rode: pip install -r requirements.txt")
        self._log(message, "error")
        messagebox.showerror("VLC necessario", message)

    def _log(self, message: str, level: str = "info", exc_info: bool = False) -> None:
        log_method = getattr(logger, level, logger.info)
        log_method(message, exc_info=exc_info)


def main() -> int:
    app = MediaPlayerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

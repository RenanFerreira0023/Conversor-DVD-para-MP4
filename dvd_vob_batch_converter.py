from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable


DEFAULT_REPORT_NAME = "relatorio_conversao_dvds.txt"
COMMON_FFMPEG_PATHS = [Path(r"C:\ffmpeg\bin\ffmpeg.exe")]


@dataclass
class FolderResult:
    folder: Path
    status: str
    vobs: list[Path] = field(default_factory=list)
    output: Path | None = None
    reason: str = ""


def natural_key(path: Path) -> tuple:
    parts = re.split(r"(\d+)", path.name.upper())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def find_vob_folders(root: Path) -> list[Path]:
    folders: set[Path] = set()
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".vob":
            folders.add(path.parent)
    return sorted(folders, key=lambda item: str(item).lower())


def find_vobs(folder: Path) -> list[Path]:
    return sorted(
        [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".vob"],
        key=natural_key,
    )


def build_concat_input(vobs: list[Path]) -> str:
    return "concat:" + "|".join(str(path.resolve()) for path in vobs)


def run_command(command: list[str], capture: bool) -> subprocess.CompletedProcess[str]:
    if capture:
        return subprocess.run(command, check=False, capture_output=True, text=True)
    return subprocess.run(command, check=False, text=True)


def find_ffmpeg() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    for path in COMMON_FFMPEG_PATHS:
        if path.exists():
            return str(path)

    return None


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}min {seconds:02d}s"
    if minutes:
        return f"{minutes}min {seconds:02d}s"
    return f"{seconds}s"


def test_video(ffmpeg: str, vobs: list[Path], seconds: int) -> tuple[bool, str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-fflags",
        "+genpts",
        "-i",
        build_concat_input(vobs),
        "-t",
        str(seconds),
        "-map",
        "0:v:0",
        "-f",
        "null",
        os.devnull,
    ]
    completed = run_command(command, capture=True)
    if completed.returncode == 0:
        return True, ""

    message = (completed.stderr or completed.stdout or "").strip()
    if not message:
        message = f"ffmpeg retornou codigo {completed.returncode} ao testar o video."
    return False, message


def convert_to_mp4(ffmpeg: str, vobs: list[Path], output: Path) -> tuple[bool, str]:
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-fflags",
        "+genpts",
        "-i",
        build_concat_input(vobs),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]
    completed = run_command(command, capture=False)
    if completed.returncode == 0:
        return True, ""
    return False, f"ffmpeg retornou codigo {completed.returncode} durante a conversao."


def write_report(report_path: Path, root: Path, results: list[FolderResult]) -> None:
    converted = [item for item in results if item.status == "convertido"]
    skipped = [item for item in results if item.status == "pulado"]
    failed = [item for item in results if item.status == "falhou"]
    dry = [item for item in results if item.status == "simulado"]

    lines: list[str] = [
        "Relatorio de conversao de DVDs (.VOB)",
        f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Raiz mapeada: {root}",
        "",
        f"Arquivos .VOB mapeados: {len(results)}",
        f"Convertidas: {len(converted)}",
        f"Puladas por ja terem MP4 correspondente: {len(skipped)}",
        f"Falhas: {len(failed)}",
        f"Simuladas: {len(dry)}",
        "",
        "Arquivos mapeados:",
    ]

    for item in results:
        vob_name = item.vobs[0].name if item.vobs else "sem VOB"
        lines.append(f"- [{item.status.upper()}] {item.folder / vob_name}")
        if item.output:
            lines.append(f"  MP4: {item.output}")
        if item.reason:
            lines.append(f"  Motivo: {item.reason}")
        if item.vobs:
            lines.append("  VOBs:")
            for vob in item.vobs:
                lines.append(f"    - {vob.name}")

    lines.append("")
    lines.append("Videos que nao conseguiram executar/converter:")
    if failed:
        for item in failed:
            vob_path = item.vobs[0] if item.vobs else item.folder
            lines.append(f"- {vob_path}")
            lines.append(f"  Motivo: {item.reason}")
    else:
        lines.append("- Nenhum.")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def process(
    root: Path,
    report: Path,
    test_seconds: int,
    dry_run: bool,
    log: Callable[[str], None] = print,
) -> int:
    ffmpeg = find_ffmpeg()
    if not ffmpeg and not dry_run:
        log("ERRO: ffmpeg nao encontrado no PATH.")
        log("Instale o FFmpeg ou adicione C:\\ffmpeg\\bin ao PATH.")
        return 1

    root = root.resolve()
    if not root.exists():
        log(f"ERRO: caminho nao encontrado: {root}")
        return 1

    folders = find_vob_folders(root)
    results: list[FolderResult] = []

    log(f"Pastas com .VOB encontradas: {len(folders)}")
    total_vobs = 0
    for folder in folders:
        total_vobs += len(find_vobs(folder))
    log(f"Arquivos .VOB encontrados: {total_vobs}")
    if total_vobs:
        log("Tempo medio: a estimativa aparece depois que o primeiro arquivo terminar.")

    current = 0
    started_at = time.monotonic()

    def log_time_estimate(processed: int) -> None:
        if processed <= 0 or total_vobs <= 0:
            return
        elapsed = time.monotonic() - started_at
        average = elapsed / processed
        remaining = average * (total_vobs - processed)
        estimated_total = average * total_vobs
        log(
            "Tempo medio por video: "
            f"{format_duration(average)} | "
            f"restante aprox.: {format_duration(remaining)} | "
            f"total aprox.: {format_duration(estimated_total)}"
        )

    for folder_index, folder in enumerate(folders, start=1):
        log(f"\n[Pasta {folder_index}/{len(folders)}] {folder}")
        vobs = find_vobs(folder)
        if not vobs:
            result = FolderResult(folder=folder, status="falhou", reason="Nenhum VOB de filme encontrado.")
            results.append(result)
            log("Falhou: nenhum VOB de filme encontrado.")
            continue

        for vob in vobs:
            current += 1
            output = vob.with_suffix(".mp4")
            log(f"\n[{current}/{total_vobs}] {vob.name}")

            if output.exists():
                result = FolderResult(
                    folder=folder,
                    status="pulado",
                    vobs=[vob],
                    output=output,
                    reason="Ja existe MP4 com o mesmo nome do VOB.",
                )
                results.append(result)
                log(f"Pulado: ja existe {output.name}.")
                log_time_estimate(current)
                continue

            if dry_run:
                result = FolderResult(folder=folder, status="simulado", vobs=[vob], output=output)
                results.append(result)
                log(f"Simulado: criaria {output.name}")
                log_time_estimate(current)
                continue

            log(f"Testando reproducao por {test_seconds}s...")
            playable, reason = test_video(ffmpeg, [vob], test_seconds)
            if not playable:
                result = FolderResult(folder=folder, status="falhou", vobs=[vob], output=output, reason=reason)
                results.append(result)
                log("Falhou no teste de reproducao.")
                log_time_estimate(current)
                continue

            log(f"Convertendo para: {output}")
            converted, reason = convert_to_mp4(ffmpeg, [vob], output)
            if converted:
                result = FolderResult(folder=folder, status="convertido", vobs=[vob], output=output)
                results.append(result)
                log("Convertido com sucesso.")
            else:
                result = FolderResult(folder=folder, status="falhou", vobs=[vob], output=output, reason=reason)
                results.append(result)
                log("Falhou durante a conversao.")
            log_time_estimate(current)

    write_report(report.resolve(), root, results)
    log(f"\nRelatorio salvo em: {report.resolve()}")

    return 1 if any(item.status == "falhou" for item in results) else 0


class ConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Conversor de DVDs VOB para MP4")
        self.geometry("860x560")
        self.minsize(680, 420)

        self.selected_folder = tk.StringVar()
        self.test_seconds = tk.IntVar(value=8)
        self.dry_run = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Escolha uma pasta para comecar.")
        self.messages: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.after(100, self._drain_messages)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(10, 6))
        style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"))

        header = ttk.Frame(self, padding=(14, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Conversor de DVDs", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(header, text="Diretorio:").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(header, textvariable=self.selected_folder).grid(row=1, column=1, sticky="ew", padx=8, pady=(12, 0))
        ttk.Button(header, text="Escolher diretorio", command=self.choose_folder).grid(row=1, column=2, pady=(12, 0))

        options = ttk.Frame(self, padding=(14, 0, 14, 10))
        options.grid(row=1, column=0, sticky="ew")
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text="Teste segundos:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=1, to=60, width=6, textvariable=self.test_seconds).grid(row=0, column=1, padx=(8, 16), sticky="w")
        ttk.Checkbutton(options, text="So mapear", variable=self.dry_run).grid(row=0, column=2, sticky="w")
        ttk.Label(options, text="O MP4 usa o mesmo nome do VOB.").grid(row=0, column=3, sticky="e")

        actions = ttk.Frame(self, padding=(14, 0, 14, 10))
        actions.grid(row=3, column=0, sticky="ew")
        actions.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(actions, text="Iniciar", command=self.start)
        self.start_button.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=12)
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=2, sticky="e")

        log_frame = ttk.Frame(self, padding=(14, 0, 14, 8))
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=16)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Escolha a pasta onde procurar arquivos .VOB")
        if folder:
            self.selected_folder.set(folder)
            self.status.set("Diretorio escolhido.")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        folder_text = self.selected_folder.get().strip()
        if not folder_text:
            messagebox.showwarning("Escolha o diretorio", "Escolha uma pasta antes de iniciar.")
            return

        root = Path(folder_text)
        if not root.exists():
            messagebox.showerror("Diretorio nao encontrado", f"Nao encontrei:\n{root}")
            return

        self.log_text.delete("1.0", tk.END)
        self.start_button.configure(state="disabled")
        self.progress.start(12)
        self.status.set("Executando...")

        report = root / DEFAULT_REPORT_NAME
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(root, report, self.test_seconds.get(), self.dry_run.get()),
            daemon=True,
        )
        self.worker.start()

    def _run_worker(self, root: Path, report: Path, test_seconds: int, dry_run: bool) -> None:
        try:
            code = process(root, report, test_seconds, dry_run, log=self._queue_log)
        except Exception as exc:
            self.messages.put(f"__DONE__|1|Erro inesperado: {exc}")
            return
        self.messages.put(f"__DONE__|{code}|{report.resolve()}")

    def _queue_log(self, message: str) -> None:
        self.messages.put(message)

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message.startswith("__DONE__|"):
                    _, code_text, detail = message.split("|", 2)
                    self._finish(int(code_text), detail)
                    continue
                self.log_text.insert(tk.END, message + "\n")
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    def _finish(self, code: int, detail: str) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        if code == 0:
            self.status.set("Concluido.")
            messagebox.showinfo("Concluido", f"Processo finalizado.\n\nRelatorio:\n{detail}")
        else:
            self.status.set("Concluido com falhas.")
            messagebox.showwarning("Concluido com falhas", f"Veja o relatorio ou o log da tela.\n\n{detail}")


def launch_gui() -> int:
    app = ConverterApp()
    app.mainloop()
    return 0


def main() -> int:
    if len(sys.argv) == 1:
        return launch_gui()

    parser = argparse.ArgumentParser(
        description="Mapeia pastas com arquivos .VOB de DVD, testa reproducao e converte para MP4."
    )
    parser.add_argument("root", nargs="?", default=".", help="Pasta raiz onde a busca recursiva vai comecar.")
    parser.add_argument(
        "-r",
        "--report",
        default=DEFAULT_REPORT_NAME,
        help=f"Caminho do relatorio. Padrao: {DEFAULT_REPORT_NAME}",
    )
    parser.add_argument(
        "--test-seconds",
        type=int,
        default=8,
        help="Quantos segundos tentar decodificar antes de converter. Padrao: 8.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas mapeia e mostra o que faria, sem converter.",
    )
    args = parser.parse_args()

    if args.test_seconds < 1:
        parser.error("--test-seconds precisa ser maior que zero.")

    return process(
        root=Path(args.root),
        report=Path(args.report),
        test_seconds=args.test_seconds,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def find_movie_vobs(video_ts: Path) -> list[Path]:
    vobs = sorted(video_ts.glob("VTS_*_[1-9].VOB"))
    if not vobs:
        vobs = sorted(video_ts.glob("*.VOB"))
    return [path for path in vobs if path.name.upper() != "VIDEO_TS.VOB"]


def build_concat_input(vobs: list[Path]) -> str:
    return "concat:" + "|".join(str(path.resolve()) for path in vobs)


def repair(video_ts: Path, output: Path) -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ERRO: ffmpeg nao encontrado no PATH.")
        print("Instale o FFmpeg ou adicione C:\\ffmpeg\\bin ao PATH.")
        return 1

    video_ts = video_ts.resolve()
    if video_ts.name.upper() != "VIDEO_TS":
        video_ts = video_ts / "VIDEO_TS"

    if not video_ts.exists():
        print(f"ERRO: pasta VIDEO_TS nao encontrada: {video_ts}")
        return 1

    vobs = find_movie_vobs(video_ts)
    if not vobs:
        print(f"ERRO: nenhum .VOB encontrado em: {video_ts}")
        return 1

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Arquivos que serao unidos/reparados:")
    for path in vobs:
        print(f"- {path.name} ({path.stat().st_size / (1024 * 1024):.1f} MB)")
    print(f"\nSaida: {output}")
    print("Isso pode demorar. Para VHS/DVD antigo, reencodar e o caminho mais confiavel.\n")

    command = [
        ffmpeg,
        "-y",
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

    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        print("\nERRO: ffmpeg terminou com falha.")
        return completed.returncode

    print("\nPronto. Abra o arquivo reparado no player:")
    print(output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Repara VOBs de DVD/VHS gerando um MP4 com tempo/busca corretos.")
    parser.add_argument("video_ts", nargs="?", default="VIDEO_TS", help="Pasta VIDEO_TS ou pasta que contem VIDEO_TS.")
    parser.add_argument("-o", "--output", default="video_reparado.mp4", help="Arquivo MP4 de saida.")
    args = parser.parse_args()
    return repair(Path(args.video_ts), Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())

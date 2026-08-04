"""
transcricao.py — Fase 1 do pipeline: extrai áudio de cada vídeo e
transcreve com faster-whisper, gravando um .txt em cache por episódio.
"""

import time

import config
from utils import format_duration
from logger import Logger
from audio import load_audio


def transcribe_all(videos, cache_dir, logger: Logger, progress_cb, stage_cb, step_cb, stop_event):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError("Instale faster-whisper: pip install faster-whisper")

    pending     = [v for v in videos if not (cache_dir / (v.stem + ".txt")).exists()]
    cache_count = len(videos) - len(pending)
    logger.stats["f1_total"] = len(videos)
    logger.stats["f1_cache"] = cache_count

    if cache_count > 0:
        logger.log(f"→ {cache_count} episódio(s) já em cache, pulando.", "cache")
    if not pending:
        logger.log("✓ Todas as transcrições já estão em cache.", "ok")
        return

    logger.log(f"Carregando Whisper '{config.cfg('whisper_model')}'...")
    t_load = time.time()
    model  = WhisperModel(
        config.cfg("whisper_model"),
        device=config.cfg("whisper_device"),
        compute_type=config.cfg("whisper_compute")
    )
    logger.log(f"Modelo Whisper carregado ({format_duration(time.time()-t_load)}).", "ok")

    for i, video in enumerate(pending, 1):
        if stop_event.is_set():
            logger.log("⚠ Interrompido pelo usuário.", "warn")
            break

        progress_cb(i, len(pending), video.name, "transcribing")
        t_video0 = time.time()
        logger.log(f"[{i}/{len(pending)}] Iniciando: {video.name}", "section")

        try:
            step_cb(0, "Extraindo áudio...")
            stage_cb("Extraindo áudio")
            t0 = time.time()
            audio = load_audio(video)
            dt_extract = time.time() - t0
            logger.log(f"  ✓ Áudio extraído ({format_duration(dt_extract)})", "ok")

            step_cb(0, "Whisper — 0%")
            stage_cb("Transcrevendo (Whisper)")
            t0             = time.time()
            total_duration = len(audio) / 16000
            segs, info     = model.transcribe(audio, language=config.cfg("whisper_language"))
            lines          = []
            for s in segs:
                lines.append(f"[{s.start:.1f}s] {s.text.strip()}")
                pct = min(s.end / total_duration * 100, 99) if total_duration > 0 else 0
                step_cb(pct, f"Whisper — {pct:.0f}%")
            step_cb(100, "Whisper — 100% ✓")
            dt_whisper = time.time() - t0

            logger.log(
                f"  Idioma detectado: {info.language} ({info.language_probability:.0%})"
            )
            logger.log(f"  ✓ Transcrição concluída ({format_duration(dt_whisper)})", "ok")

            txt = "\n".join(lines)
            if not txt.strip():
                logger.log("  ⚠ Transcrição vazia.", "warn")
                logger.stats["f1_err"] += 1
                logger.stats["errors"].append(f"Transcrição vazia: {video.name}")
                continue

            (cache_dir / (video.stem + ".txt")).write_text(txt, encoding="utf-8")
            dt_total = time.time() - t_video0
            logger.stats["f1_ok"] += 1
            logger.stats["f1_durations"].append(dt_total)
            ultimo_ts = lines[-1].split("]")[0].strip("[") if lines else "?"
            logger.log(
                f"  ✓ Vídeo concluído — total: {format_duration(dt_total)} "
                f"(áudio {format_duration(dt_extract)} + whisper {format_duration(dt_whisper)}) "
                f"| último timestamp no áudio: {ultimo_ts}",
                "ok"
            )

        except Exception as e:
            logger.log(f"  ✗ Erro: {e}", "err")
            logger.stats["f1_err"] += 1
            logger.stats["errors"].append(f"{video.name}: {e}")

    del model
    logger.log("Whisper descarregado da VRAM.")

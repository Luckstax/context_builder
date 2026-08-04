"""
pipeline.py — Orquestra as duas fases (transcrição e análise) e grava
o context.json final. É a única camada que a GUI chama diretamente.
"""

import json
import re
import time
from pathlib import Path

import config
from utils import format_duration
from logger import Logger
from transcricao import transcribe_all
from lms_client import ensure_lmstudio, get_client
from analise import analyze_episode, merge_contexts


def sort_episodes(paths):
    def key(p):
        m = re.search(r"[Ss](\d+)[Ee](\d+)", p.stem)
        if m: return (int(m.group(1)), int(m.group(2)))
        m = re.search(r"(\d+)[xX](\d+)", p.stem)
        if m: return (int(m.group(1)), int(m.group(2)))
        return (999, 999)
    return sorted(paths, key=key)


def run_pipeline(phase, logger: Logger, progress_cb, stage_cb, step_cb, stop_event):
    try:
        cache_dir = Path(config.cfg("pasta_transcricoes"))
        ctx_dir   = Path(config.cfg("pasta_analises"))
        for folder in [cache_dir, ctx_dir]:
            folder.mkdir(parents=True, exist_ok=True)

        EXTS   = {".mkv", ".mp4", ".avi", ".mov", ".m4v"}
        videos = sort_episodes([
            p for p in Path(config.cfg("videos_dir")).rglob("*")
            if p.suffix.lower() in EXTS
        ])
        logger.log(f"{len(videos)} episódio(s) encontrados em {config.cfg('videos_dir')}")

        if not videos:
            logger.log("✗ Nenhum vídeo encontrado.", "err")
            return

        if phase in ("transcribe", "both"):
            logger.log("── FASE 1: TRANSCRIÇÃO ──────────────────────", "section")
            transcribe_all(videos, cache_dir, logger, progress_cb, stage_cb, step_cb, stop_event)
            if stop_event.is_set():
                return
            logger.log("Fase 1 concluída.", "ok")

        if phase == "transcribe":
            return

        logger.log("── FASE 2: ANÁLISE ──────────────────────────", "section")
        ensure_lmstudio(logger)
        if stop_event.is_set():
            return

        client = get_client()
        all_eps = []
        logger.stats["f2_total"] = len(videos)

        for i, video in enumerate(videos, 1):
            if stop_event.is_set():
                logger.log("⚠ Interrompido pelo usuário.", "warn")
                break

            tx = cache_dir / (video.stem + ".txt")
            if not tx.exists():
                logger.log(f"⚠ Sem transcrição: {video.name}", "warn")
                logger.stats["f2_err"] += 1
                continue

            transcript = tx.read_text(encoding="utf-8")
            if not transcript.strip():
                logger.stats["f2_err"] += 1
                continue

            progress_cb(i, len(videos), video.name, "analyzing")
            step_cb(0, "")
            t_video0 = time.time()
            logger.log(f"[{i}/{len(videos)}] Iniciando análise: {video.name}", "section")

            try:
                ep, from_cache = analyze_episode(
                    client, video.stem, transcript, ctx_dir, logger, stage_cb, step_cb
                )
                all_eps.append(ep)
                dt_total = time.time() - t_video0
                if from_cache:
                    logger.stats["f2_cache"] += 1
                else:
                    logger.stats["f2_ok"] += 1
                    logger.stats["f2_durations"].append(dt_total)
                    chars  = len(ep.get("characters_seen", []))
                    events = len(ep.get("plot_events", []))
                    logger.log(
                        f"  ✓ {chars} personagens | {events} eventos "
                        f"({format_duration(dt_total)})", "ok"
                    )
            except Exception as e:
                logger.log(f"  ✗ Erro: {e}", "err")
                logger.stats["f2_err"] += 1
                logger.stats["errors"].append(f"{video.name}: {e}")

        if all_eps:
            final = merge_contexts(client, all_eps, logger)
            out   = Path(config.cfg("arquivo_final"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.log(f"✓ context.json salvo em {out}", "ok")
            logger.log(f"  Personagens: {len(final.get('characters', []))}", "ok")
            logger.log(f"  Glossário:   {len(final.get('glossary', []))}", "ok")
            logger.log(f"  Locais:      {len(final.get('locations', []))}", "ok")
        else:
            logger.log("✗ Nenhum episódio analisado com sucesso.", "err")

    except Exception as e:
        import traceback
        logger.log(f"✗ Erro crítico: {e}", "err")
        logger.log(traceback.format_exc(), "err")

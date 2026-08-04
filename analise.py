"""
analise.py — Fase 2 do pipeline: analisa cada transcrição com o LM
Studio (por chunks) e consolida tudo em um context.json final.
"""

import json
import re
import time
from pathlib import Path

import config
from utils import format_duration
from logger import Logger
from lms_client import call_lms


# ─── Cache por episódio ──────────────────────────────────────────

def ep_cache_load(stem: str, ctx_dir: Path):
    f = ctx_dir / (stem + ".json")
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def ep_cache_save(stem: str, data: dict, ctx_dir: Path):
    f = ctx_dir / (stem + ".json")
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Análise por episódio ──────────────────────────────────────────

def analyze_episode(client, name, transcript, ctx_dir, logger: Logger, stage_cb=None, step_cb=None):
    cached = ep_cache_load(name, ctx_dir)
    if cached:
        logger.log("  → Cache.", "cache")
        return cached, True

    words      = transcript.split()
    chunk_size = config.cfg("chunk_minutes") * 150
    chunks     = [
        " ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)
    ]
    ctx = {
        "episode": name, "characters_seen": [], "plot_events": [],
        "new_terms": [], "locations": [], "raw_notes": []
    }

    for i, chunk in enumerate(chunks, 1):
        if stage_cb:
            stage_cb(f"Analisando (LM Studio) — chunk {i}/{len(chunks)}")
        if step_cb:
            step_cb((i-1) / len(chunks) * 100, f"LM Studio — chunk {i}/{len(chunks)}")
        t0 = time.time()
        logger.log(f"  Chunk {i}/{len(chunks)}...")

        prompt = f"""Analise o episódio "{name}" de {config.cfg('series_name')} (dublado em português brasileiro).

TRANSCRIÇÃO (parte {i}/{len(chunks)}):
{chunk}

Retorne APENAS JSON sem markdown:
{{
  "characters_seen": [{{"name": "Nome", "description": "papel", "relationships": ["relações"]}}],
  "plot_events": ["evento 1"],
  "new_terms": [{{"term": "termo", "meaning": "significado"}}],
  "locations": ["local"],
  "notes": ["observação para legendagem"]
}}
Priorize nomes e termos que NÃO devem ser traduzidos nas legendas."""

        try:
            raw = call_lms(client, prompt)
            raw = re.sub(r"^```json\s*|\s*```$", "", raw).strip()
            d   = json.loads(raw)
            ctx["characters_seen"].extend(d.get("characters_seen", []))
            ctx["plot_events"].extend(d.get("plot_events", []))
            ctx["new_terms"].extend(d.get("new_terms", []))
            ctx["locations"].extend(d.get("locations", []))
            ctx["raw_notes"].extend(d.get("notes", []))
            logger.log(f"    ✓ Chunk {i} concluído ({format_duration(time.time()-t0)})", "ok")
        except json.JSONDecodeError:
            logger.log(f"  ⚠ JSON inválido no chunk {i}.", "warn")
            ctx["raw_notes"].append(f"[chunk {i}]: JSON inválido")
        except Exception as e:
            logger.log(f"  ✗ {e}", "err")

    if step_cb:
        step_cb(100, "LM Studio — ✓")
    ep_cache_save(name, ctx, ctx_dir)
    return ctx, False


# ─── Consolidação final ──────────────────────────────────────────

def _merge_prompt(series, data, count_desc):
    return f"""Dados de {count_desc} de {series}.
{data}
Consolide em JSON para legendagem. Retorne APENAS JSON:
{{
  "series": "{series}",
  "characters": [{{"name": "Nome canônico", "role": "papel",
    "description": "quem é", "alter_ego": "se houver",
    "relationships": {{}}, "first_seen": "S01E01"}}],
  "plot_summary": {{"main_arc": "arco", "seasons": {{}}, "current_status": "status"}},
  "glossary": [{{"term": "termo", "type": "tipo", "meaning": "significado",
    "do_not_translate": true, "notes": "nota"}}],
  "locations": [{{"name": "local", "description": "o que é", "do_not_translate": true}}],
  "subtitle_rules": [
    "Cat Noir → manter como Cat Noir",
    "Ladybug → manter como Ladybug",
    "kwami → manter como kwami"
  ]
}}"""


def _call_merge(client, series, items, count_desc, logger: Logger, label):
    prompt = _merge_prompt(series, json.dumps(items, ensure_ascii=False, indent=2), count_desc)
    try:
        raw = call_lms(client, prompt, max_tokens=4096)
        raw = re.sub(r"^```json\s*|\s*```$", "", raw).strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.log(f"  ⚠ JSON inválido em {label}. Mantendo dados brutos.", "warn")
        return {"raw": raw, "source_items": items}
    except Exception as e:
        logger.log(f"  ⚠ Erro em {label}: {e}", "warn")
        return {"raw": None, "source_items": items, "error": str(e)}


def merge_contexts(client, episodes, logger: Logger):
    t0     = time.time()
    series = config.cfg("series_name")

    # Limite conservador por lote — dimensionado para o context_length
    # configurado (padrão 8192), com folga para o prompt fixo + resposta.
    BATCH_SIZE = 15

    if len(episodes) <= BATCH_SIZE:
        logger.log("Consolidando contexto final...")
        result = _call_merge(
            client, series, episodes, f"{len(episodes)} episódios", logger, "merge final"
        )
        logger.log(f"  ✓ Contexto consolidado ({format_duration(time.time()-t0)})", "ok")
        return result

    batches = [episodes[i:i+BATCH_SIZE] for i in range(0, len(episodes), BATCH_SIZE)]
    logger.log(
        f"Consolidando em {len(batches)} lote(s) de até {BATCH_SIZE} episódios "
        f"(dataset grande — preservando detalhe em vez de cortar dados)...",
        "cache"
    )
    partials = []
    for bi, batch in enumerate(batches, 1):
        logger.log(f"  Lote {bi}/{len(batches)}...")
        tb = time.time()
        partials.append(_call_merge(
            client, series, batch,
            f"{len(batch)} episódios (lote {bi}/{len(batches)})",
            logger, f"lote {bi}"
        ))
        logger.log(f"  ✓ Lote {bi} concluído ({format_duration(time.time()-tb)})", "ok")

    logger.log("  Consolidando lotes em contexto final...")
    result = _call_merge(
        client, series, partials, f"{len(partials)} lotes pré-consolidados",
        logger, "merge final"
    )
    logger.log(f"  ✓ Contexto consolidado ({format_duration(time.time()-t0)})", "ok")
    return result

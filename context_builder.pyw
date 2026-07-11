"""
context_builder.py — Pipeline de contexto para séries animadas
==============================================================
Transcreve episódios dublados e gera context.json estruturado
para uso em legendagem assistida por IA.

Configuração via config.json (criado automaticamente na primeira execução)
ou diretamente pelo menu Configurações dentro do programa.

INSTALAÇÃO:
    pip install openai faster-whisper
    pip install torch --index-url https://download.pytorch.org/whl/cu121
    (tkinter já vem com Python)
"""

import os, json, subprocess, shutil, time, re, queue, threading, urllib.request
from datetime import datetime
from pathlib import Path
from openai import OpenAI
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ══════════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO — carregada de config.json
# ══════════════════════════════════════════════════════════════════

CONFIG_FILE = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "series_name":        "Miraculous: As Aventuras de Ladybug",
    "videos_dir":         "",
    "pasta_transcricoes": "",
    "pasta_analises":     "",
    "arquivo_final":      "",
    "arquivo_log":        "",
    "whisper_model":      "medium",
    "whisper_language":   "pt",
    "whisper_device":     "cpu",
    "whisper_compute":    "int8",
    "lms_model_id":       "unsloth/Phi-4-mini-instruct-GGUF",
    "lms_model_quant":    "q4_k_m",
    "lms_port":           1234,
    "chunk_minutes":      20,
    "max_tokens":         2048
}

CONFIG: dict = {}


def load_config():
    global CONFIG
    if CONFIG_FILE.exists():
        try:
            saved  = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            CONFIG = {**DEFAULT_CONFIG, **saved}
            return
        except Exception:
            pass
    CONFIG = DEFAULT_CONFIG.copy()
    save_config()


def save_config():
    CONFIG_FILE.write_text(
        json.dumps(CONFIG, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def cfg(key: str):
    return CONFIG.get(key, DEFAULT_CONFIG.get(key))


def lms_base_url() -> str:
    return f"http://localhost:{cfg('lms_port')}/v1"


# ══════════════════════════════════════════════════════════════════


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


# ─── Logger ──────────────────────────────────────────────────────

class Logger:
    def __init__(self, gui_queue: queue.Queue):
        self.queue = gui_queue
        self._f    = None
        self.t0    = None
        self.stats = dict(
            f1_total=0, f1_ok=0, f1_err=0, f1_cache=0,
            f2_total=0, f2_ok=0, f2_err=0, f2_cache=0,
            f1_durations=[], f2_durations=[],
            errors=[]
        )

    def open(self):
        log_path = Path(cfg("arquivo_log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(log_path, "w", encoding="utf-8")
        self.t0 = datetime.now()
        self._raw(
            f"LOG DE EXECUÇÃO\n"
            f"Data: {self.t0:%d/%m/%Y %H:%M:%S}\n"
            f"Série: {cfg('series_name')}\n\n"
        )

    def close(self):
        self._summary()
        if self._f:
            self._f.close()

    def log(self, msg: str, tag: str = "normal"):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.queue.put(("log", line + "\n", tag))
        self._raw(line + "\n")

    def _raw(self, text: str):
        if self._f:
            self._f.write(text)
            self._f.flush()

    def _summary(self):
        s  = self.stats
        dt = datetime.now() - self.t0 if self.t0 else None
        h = r = m = sec = 0
        if dt:
            h, r   = divmod(int(dt.total_seconds()), 3600)
            m, sec = divmod(r, 60)
        lines = [
            "", "═"*50, "RELATÓRIO FINAL", "═"*50,
            f"Duração total: {h}h {m:02d}min {sec:02d}s",
        ]
        if s["f1_total"] > 0:
            lines += [
                "", "FASE 1 — TRANSCRIÇÃO",
                f"  ✓ Transcritos : {s['f1_ok']} / {s['f1_total']}",
                f"  → Cache       : {s['f1_cache']}",
                f"  ✗ Erros       : {s['f1_err']}",
            ]
            if s["f1_durations"]:
                avg = sum(s["f1_durations"]) / len(s["f1_durations"])
                lines.append(
                    f"  Tempo médio/ep: {format_duration(avg)} "
                    f"(min {format_duration(min(s['f1_durations']))} / "
                    f"máx {format_duration(max(s['f1_durations']))})"
                )
        if s["f2_total"] > 0:
            lines += [
                "", "FASE 2 — ANÁLISE",
                f"  ✓ Analisados  : {s['f2_ok']} / {s['f2_total']}",
                f"  → Cache       : {s['f2_cache']}",
                f"  ✗ Erros       : {s['f2_err']}",
            ]
            if s["f2_durations"]:
                avg = sum(s["f2_durations"]) / len(s["f2_durations"])
                lines.append(
                    f"  Tempo médio/ep: {format_duration(avg)} "
                    f"(min {format_duration(min(s['f2_durations']))} / "
                    f"máx {format_duration(max(s['f2_durations']))})"
                )
        if s["errors"]:
            lines += ["", "ERROS DETALHADOS"]
            for e in s["errors"]:
                lines.append(f"  • {e}")
        text = "\n".join(lines) + "\n"
        self._raw(text)
        self.queue.put(("log", text, "warn"))


# ─── Áudio via PyAV ──────────────────────────────────────────────

def load_audio(video_path: Path):
    import av, numpy as np
    c       = av.open(str(video_path))
    streams = [s for s in c.streams if s.type == "audio"]
    if not streams:
        c.close()
        raise RuntimeError("Nenhuma faixa de áudio encontrada.")
    rs     = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    chunks = []
    def absorb(frame):
        if frame is None:
            return
        arr = frame.to_ndarray()
        chunks.append(arr[0] if arr.ndim == 2 else arr)
    for pkt in c.demux(streams[0]):
        for frm in pkt.decode():
            for rf in rs.resample(frm):
                absorb(rf)
    for rf in rs.resample(None):
        absorb(rf)
    c.close()
    if not chunks:
        raise RuntimeError("Áudio vazio após extração.")
    return np.concatenate(chunks)


# ─── LM Studio ───────────────────────────────────────────────────

def find_lms() -> str:
    if shutil.which("lms"):
        return "lms"
    for p in [
        os.path.expandvars(r"%USERPROFILE%\.lmstudio\bin\lms.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\LM-Studio\bin\lms.exe"),
    ]:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "CLI do LM Studio não encontrado. Abra o LM Studio pelo menos uma vez."
    )


def is_lms_running() -> bool:
    try:
        urllib.request.urlopen(
            f"http://localhost:{cfg('lms_port')}/v1/models", timeout=3
        )
        return True
    except Exception:
        return False


def ensure_lmstudio(logger: Logger):
    if is_lms_running():
        logger.log("LM Studio já está rodando.", "ok")
        return
    t0  = time.time()
    lms = find_lms()
    logger.log("Iniciando LM Studio daemon...")
    r = subprocess.run([lms, "daemon", "up"], capture_output=True, text=True)
    if r.returncode != 0 and "already" not in r.stderr.lower():
        logger.log(f"⚠ Daemon: {r.stderr[:120]}", "warn")
    time.sleep(2)
    ls = subprocess.run([lms, "ls"], capture_output=True, text=True)
    model_short = cfg("lms_model_id").split("/")[-1].lower().split("-gguf")[0]
    if model_short not in ls.stdout.lower():
        spec = f"{cfg('lms_model_id')}@{cfg('lms_model_quant')}"
        logger.log(f"Baixando modelo {spec}...", "warn")
        subprocess.run([lms, "get", spec])
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [lms, "server", "start", "--port", str(cfg("lms_port"))], **kw
    )
    logger.log("Aguardando LM Studio iniciar...")
    for _ in range(25):
        time.sleep(1)
        if is_lms_running():
            logger.log(f"LM Studio pronto ({format_duration(time.time()-t0)}).", "ok")
            return
    raise RuntimeError("LM Studio não respondeu após 25 segundos.")


def get_client() -> OpenAI:
    return OpenAI(base_url=lms_base_url(), api_key="lm-studio")


def call_lms(client: OpenAI, prompt: str, max_tokens: int = None) -> str:
    mt = max_tokens or cfg("max_tokens")
    r  = client.chat.completions.create(
        model=cfg("lms_model_id"), max_tokens=mt,
        messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()


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


# ─── Transcrição ─────────────────────────────────────────────────

def transcribe_all(videos, cache_dir, logger, progress_cb, stage_cb, step_cb, stop_event):
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

    logger.log(f"Carregando Whisper '{cfg('whisper_model')}'...")
    t_load = time.time()
    model  = WhisperModel(
        cfg("whisper_model"),
        device=cfg("whisper_device"),
        compute_type=cfg("whisper_compute")
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
            segs, info     = model.transcribe(audio, language=cfg("whisper_language"))
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


# ─── Análise ─────────────────────────────────────────────────────

def analyze_episode(client, name, transcript, ctx_dir, logger, stage_cb=None, step_cb=None):
    cached = ep_cache_load(name, ctx_dir)
    if cached:
        logger.log("  → Cache.", "cache")
        return cached, True

    words      = transcript.split()
    chunk_size = cfg("chunk_minutes") * 150
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

        prompt = f"""Analise o episódio "{name}" de {cfg('series_name')} (dublado em português brasileiro).

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


def merge_contexts(client, episodes, logger):
    logger.log("Consolidando contexto final...")
    t0   = time.time()
    data = json.dumps(episodes, ensure_ascii=False, indent=2)
    if len(data) > 60000:
        logger.log("  (resumindo — dataset grande)", "cache")
        data = json.dumps([{
            "episode":         ep["episode"],
            "characters_seen": [c["name"] for c in ep.get("characters_seen", [])],
            "plot_events":     ep.get("plot_events", [])[:5],
            "new_terms":       ep.get("new_terms", [])[:10]
        } for ep in episodes], ensure_ascii=False, indent=2)

    series = cfg("series_name")
    prompt = f"""Dados de {len(episodes)} episódios de {series}.
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

    try:
        raw = call_lms(client, prompt, max_tokens=4096)
        raw = re.sub(r"^```json\s*|\s*```$", "", raw).strip()
        result = json.loads(raw)
        logger.log(f"  ✓ Contexto consolidado ({format_duration(time.time()-t0)})", "ok")
        return result
    except json.JSONDecodeError:
        logger.log("⚠ Erro no merge final. Salvando conteúdo bruto.", "warn")
        return {"raw": raw, "episodes": episodes}


def sort_episodes(paths):
    def key(p):
        m = re.search(r"[Ss](\d+)[Ee](\d+)", p.stem)
        if m: return (int(m.group(1)), int(m.group(2)))
        m = re.search(r"(\d+)[xX](\d+)", p.stem)
        if m: return (int(m.group(1)), int(m.group(2)))
        return (999, 999)
    return sorted(paths, key=key)


# ─── Pipeline ────────────────────────────────────────────────────

def run_pipeline(phase, logger, progress_cb, stage_cb, step_cb, stop_event):
    try:
        cache_dir = Path(cfg("pasta_transcricoes"))
        ctx_dir   = Path(cfg("pasta_analises"))
        for folder in [cache_dir, ctx_dir]:
            folder.mkdir(parents=True, exist_ok=True)

        EXTS   = {".mkv", ".mp4", ".avi", ".mov", ".m4v"}
        videos = sort_episodes([
            p for p in Path(cfg("videos_dir")).rglob("*")
            if p.suffix.lower() in EXTS
        ])
        logger.log(f"{len(videos)} episódio(s) encontrados em {cfg('videos_dir')}")

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
            out   = Path(cfg("arquivo_final"))
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


# ══════════════════════════════════════════════════════════════════
#  JANELA DE CONFIGURAÇÕES
# ══════════════════════════════════════════════════════════════════

class SettingsWindow(tk.Toplevel):
    BG    = "#1a1b26"
    PANEL = "#24283b"
    TEXT  = "#c0caf5"
    GRAY  = "#565f89"
    ACCENT = "#7aa2f7"
    GREEN = "#9ece6a"

    def __init__(self, parent, on_save_callback):
        super().__init__(parent)
        self.title("Configurações")
        self.configure(bg=self.BG)
        self.resizable(False, True)
        self.grab_set()   # modal
        self._on_save = on_save_callback
        self._entries  = {}
        self._build()
        self._load_values()
        self.update_idletasks()
        # Centralizar em relação ao pai
        x = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    # ── Construção ───────────────────────────────────────────────

    def _build(self):
        # Container com scroll
        canvas = tk.Canvas(self, bg=self.BG, highlightthickness=0, width=520)
        sb     = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        frame = tk.Frame(canvas, bg=self.BG)
        canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        ))

        tk.Label(frame, text="⚙  Configurações", bg=self.BG, fg=self.ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(pady=(14, 10), padx=20, anchor="w")

        # ── Seção Geral ──────────────────────────────────────────
        self._section(frame, "GERAL")
        self._field(frame, "series_name",    "Nome da série")
        self._field(frame, "whisper_language", "Idioma do áudio (pt, fr, es…)")

        # ── Seção Pastas ─────────────────────────────────────────
        self._section(frame, "PASTAS")

        # Botão de atalho: definir tudo a partir de uma pasta base
        base_frame = tk.Frame(frame, bg=self.BG, padx=20)
        base_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(base_frame, text="Definir pasta de saída base →",
                 bg=self.BG, fg=self.GRAY, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        tk.Button(base_frame, text="📁 Escolher",
                  bg=self.PANEL, fg=self.TEXT, relief=tk.FLAT,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=self._set_base_output
                  ).pack(side=tk.LEFT, padx=6)

        self._field(frame, "videos_dir",        "Pasta dos vídeos",    folder=True)
        self._field(frame, "pasta_transcricoes", "Pasta de transcrições", folder=True)
        self._field(frame, "pasta_analises",     "Pasta de análises",   folder=True)
        self._field(frame, "arquivo_final",      "Arquivo context.json")
        self._field(frame, "arquivo_log",        "Arquivo de log (.txt)")

        # ── Seção Whisper ────────────────────────────────────────
        self._section(frame, "WHISPER")
        self._combo(frame, "whisper_model",   "Modelo",
                    ["tiny", "base", "small", "medium", "large-v2", "large-v3"])
        self._combo(frame, "whisper_device",  "Dispositivo", ["cpu", "cuda"])
        self._combo(frame, "whisper_compute", "Quantização", ["int8", "float16", "float32"])

        # ── Seção LM Studio ──────────────────────────────────────
        self._section(frame, "LM STUDIO")
        self._field(frame, "lms_model_id",   "ID do modelo")
        self._field(frame, "lms_model_quant", "Quantização (q4_k_m, q8_0…)")
        self._field(frame, "lms_port",       "Porta")

        # ── Botões ───────────────────────────────────────────────
        bf = tk.Frame(frame, bg=self.BG, pady=14)
        bf.pack()
        tk.Button(bf, text="💾  Salvar", bg=self.ACCENT, fg=self.BG,
                  font=("Segoe UI", 10, "bold"), relief=tk.FLAT,
                  padx=20, pady=6, cursor="hand2",
                  command=self._save
                  ).pack(side=tk.LEFT, padx=8)
        tk.Button(bf, text="Cancelar", bg=self.PANEL, fg=self.TEXT,
                  font=("Segoe UI", 10), relief=tk.FLAT,
                  padx=20, pady=6, cursor="hand2",
                  command=self.destroy
                  ).pack(side=tk.LEFT, padx=8)

    def _section(self, parent, title):
        f = tk.Frame(parent, bg=self.BG, padx=20)
        f.pack(fill=tk.X, pady=(10, 2))
        tk.Label(f, text=title, bg=self.BG, fg=self.ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Frame(parent, bg=self.GRAY, height=1).pack(fill=tk.X, padx=20, pady=(0, 6))

    def _field(self, parent, key, label, folder=False):
        f = tk.Frame(parent, bg=self.BG, padx=20)
        f.pack(fill=tk.X, pady=3)
        tk.Label(f, text=label + ":", bg=self.BG, fg=self.GRAY,
                 font=("Segoe UI", 9), width=28, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar()
        self._entries[key] = var
        entry = tk.Entry(f, textvariable=var, bg=self.PANEL, fg=self.TEXT,
                         insertbackground=self.TEXT, relief=tk.FLAT,
                         font=("Consolas", 9), width=32)
        entry.pack(side=tk.LEFT, padx=(0, 4))
        if folder:
            tk.Button(f, text="📁", bg=self.PANEL, fg=self.TEXT, relief=tk.FLAT,
                      cursor="hand2", font=("Segoe UI", 9),
                      command=lambda k=key: self._browse_folder(k)
                      ).pack(side=tk.LEFT)

    def _combo(self, parent, key, label, values):
        f = tk.Frame(parent, bg=self.BG, padx=20)
        f.pack(fill=tk.X, pady=3)
        tk.Label(f, text=label + ":", bg=self.BG, fg=self.GRAY,
                 font=("Segoe UI", 9), width=28, anchor="w").pack(side=tk.LEFT)
        var = tk.StringVar()
        self._entries[key] = var
        cb = ttk.Combobox(f, textvariable=var, values=values, width=18,
                          state="readonly", font=("Consolas", 9))
        cb.pack(side=tk.LEFT)

    # ── Ações ────────────────────────────────────────────────────

    def _load_values(self):
        for key, var in self._entries.items():
            var.set(str(cfg(key)))

    def _browse_folder(self, key):
        current = self._entries[key].get()
        initial = current if Path(current).exists() else str(Path.home())
        chosen  = filedialog.askdirectory(title="Selecionar pasta", initialdir=initial)
        if chosen:
            self._entries[key].set(chosen.replace("/", "\\"))

    def _set_base_output(self):
        chosen = filedialog.askdirectory(
            title="Selecionar pasta de saída base",
            initialdir=str(Path.home())
        )
        if not chosen:
            return
        base = chosen.replace("/", "\\")
        self._entries["pasta_transcricoes"].set(base + "\\transcricoes")
        self._entries["pasta_analises"].set(base + "\\analises")
        self._entries["arquivo_final"].set(base + "\\context.json")
        self._entries["arquivo_log"].set(base + "\\relatorio.txt")

    def _save(self):
        for key, var in self._entries.items():
            val = var.get().strip()
            # Converte porta para int
            if key == "lms_port":
                try:
                    val = int(val)
                except ValueError:
                    messagebox.showerror("Erro", "Porta do LM Studio deve ser um número.")
                    return
            CONFIG[key] = val
        save_config()
        self._on_save()
        self.destroy()


# ══════════════════════════════════════════════════════════════════
#  JANELA PRINCIPAL
# ══════════════════════════════════════════════════════════════════

class App(tk.Tk):
    BG     = "#1a1b26"
    PANEL  = "#24283b"
    TEXT   = "#c0caf5"
    ACCENT = "#7aa2f7"
    GREEN  = "#9ece6a"
    RED    = "#f7768e"
    ORANGE = "#e0af68"
    BLUE   = "#7dcfff"
    GRAY   = "#565f89"

    def __init__(self):
        super().__init__()
        self.title("Context Builder")
        self.configure(bg=self.BG)
        self.geometry("740x680")
        self.resizable(True, True)
        self.minsize(600, 540)

        self._queue        = queue.Queue()
        self._stop         = threading.Event()
        self._phase        = tk.StringVar(value="both")
        self._running      = False
        self._stage_name   = ""
        self._stage_start  = None
        self._path_labels  = {}

        self._build()
        self._poll()

    # ── Construção da UI ─────────────────────────────────────────

    def _build(self):
        # Cabeçalho
        header = tk.Frame(self, bg=self.BG)
        header.pack(fill=tk.X, padx=16, pady=(14, 2))
        tk.Label(header, text="🐞  Context Builder",
                 bg=self.BG, fg=self.ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        tk.Button(header, text="⚙ Configurações",
                  bg=self.PANEL, fg=self.TEXT, relief=tk.FLAT,
                  font=("Segoe UI", 9), padx=10, pady=3,
                  cursor="hand2", command=self._open_settings
                  ).pack(side=tk.RIGHT)

        self._series_label = tk.Label(self, text=cfg("series_name"),
                                       bg=self.BG, fg=self.GRAY,
                                       font=("Segoe UI", 9))
        self._series_label.pack(pady=(0, 10))

        # Painel de pastas
        pf = tk.Frame(self, bg=self.PANEL, padx=16, pady=10)
        pf.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(pf, text="Pastas", bg=self.PANEL, fg=self.ACCENT,
                 font=("Segoe UI", 9, "bold")).grid(
                     row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self._path_rows = [
            ("Vídeos",       "videos_dir"),
            ("Transcrições", "pasta_transcricoes"),
            ("Análises",     "pasta_analises"),
            ("Resultado",    "arquivo_final"),
            ("Log",          "arquivo_log"),
        ]
        for i, (label, key) in enumerate(self._path_rows, 1):
            tk.Label(pf, text=f"{label}:", bg=self.PANEL, fg=self.GRAY,
                     font=("Segoe UI", 9), anchor="w", width=13
                     ).grid(row=i, column=0, sticky="w")
            lbl = tk.Label(pf, text=cfg(key) or "(não configurado)",
                           bg=self.PANEL,
                           fg=self.TEXT if cfg(key) else self.RED,
                           font=("Consolas", 9), anchor="w")
            lbl.grid(row=i, column=1, sticky="w")
            self._path_labels[key] = lbl

        # Fase
        ff = tk.Frame(self, bg=self.BG)
        ff.pack(pady=6)
        tk.Label(ff, text="Fase:", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 10))
        for label, val in [("Fase 1 — Transcrição", "transcribe"),
                             ("Fase 2 — Análise",     "analyze"),
                             ("Ambas",                "both")]:
            ttk.Radiobutton(ff, text=label, variable=self._phase,
                            value=val).pack(side=tk.LEFT, padx=6)

        # Progresso geral
        pgf = tk.Frame(self, bg=self.BG, padx=16)
        pgf.pack(fill=tk.X, pady=(0, 4))

        self._ep_label = tk.Label(pgf, text="Aguardando...",
                                   bg=self.BG, fg=self.GRAY,
                                   font=("Segoe UI", 9))
        self._ep_label.pack(anchor="w")

        self._prog_var = tk.DoubleVar(value=0)
        sty = ttk.Style()
        sty.theme_use("clam")
        sty.configure("M.Horizontal.TProgressbar",
                       troughcolor=self.PANEL, background=self.ACCENT, thickness=12)
        ttk.Progressbar(pgf, variable=self._prog_var, maximum=100,
                        style="M.Horizontal.TProgressbar"
                        ).pack(fill=tk.X, pady=(2, 0))

        self._stage_label = tk.Label(pgf, text="",
                                      bg=self.BG, fg=self.BLUE,
                                      font=("Consolas", 9))
        self._stage_label.pack(anchor="w", pady=(3, 0))

        # Progresso por etapa
        self._step_label = tk.Label(pgf, text="",
                                     bg=self.BG, fg=self.GRAY,
                                     font=("Segoe UI", 8))
        self._step_label.pack(anchor="w", pady=(5, 0))
        self._step_prog_var = tk.DoubleVar(value=0)
        sty.configure("Step.Horizontal.TProgressbar",
                       troughcolor=self.PANEL, background=self.GREEN, thickness=8)
        ttk.Progressbar(pgf, variable=self._step_prog_var, maximum=100,
                        style="Step.Horizontal.TProgressbar"
                        ).pack(fill=tk.X, pady=(1, 4))

        # Log
        lf = tk.Frame(self, bg=self.BG, padx=16)
        lf.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        tk.Label(lf, text="Log:", bg=self.BG, fg=self.GRAY,
                 font=("Segoe UI", 9)).pack(anchor="w")

        self._log = tk.Text(lf, bg=self.PANEL, fg=self.TEXT,
                             font=("Consolas", 9), wrap=tk.WORD,
                             relief=tk.FLAT, state=tk.DISABLED,
                             selectbackground=self.ACCENT)
        sb2 = tk.Scrollbar(lf, command=self._log.yview, bg=self.PANEL)
        self._log.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(fill=tk.BOTH, expand=True)

        for tag, color in [("ok", self.GREEN), ("err", self.RED),
                             ("warn", self.ORANGE), ("cache", self.BLUE),
                             ("normal", self.TEXT)]:
            self._log.tag_config(tag, foreground=color)
        self._log.tag_config("section", foreground=self.ACCENT,
                              font=("Consolas", 9, "bold"))

        # Botões
        bf = tk.Frame(self, bg=self.BG, pady=10)
        bf.pack()
        self._btn_start = tk.Button(bf, text="▶  INICIAR",
                                     bg=self.ACCENT, fg=self.BG,
                                     font=("Segoe UI", 10, "bold"),
                                     relief=tk.FLAT, padx=24, pady=7,
                                     cursor="hand2", command=self._start)
        self._btn_start.pack(side=tk.LEFT, padx=6)

        self._btn_stop = tk.Button(bf, text="■  PARAR",
                                    bg=self.GRAY, fg=self.TEXT,
                                    font=("Segoe UI", 10), relief=tk.FLAT,
                                    padx=24, pady=7, cursor="hand2",
                                    state=tk.DISABLED, command=self._stop_click)
        self._btn_stop.pack(side=tk.LEFT, padx=6)

    # ── Atualiza labels das pastas ────────────────────────────────

    def _refresh_paths(self):
        self._series_label.config(text=cfg("series_name"))
        for _, key in self._path_rows:
            val = cfg(key)
            lbl = self._path_labels[key]
            lbl.config(
                text=val or "(não configurado)",
                fg=self.TEXT if val else self.RED
            )

    # ── Configurações ─────────────────────────────────────────────

    def _open_settings(self):
        SettingsWindow(self, on_save_callback=self._refresh_paths)

    # ── Log e fila ───────────────────────────────────────────────

    def _write_log(self, text, tag="normal"):
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text, tag)
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _poll(self):
        try:
            while True:
                msg  = self._queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    _, text, tag = msg
                    self._write_log(text, tag)
                elif kind == "progress":
                    _, cur, total, name, status = msg
                    pct  = (cur / total * 100) if total else 0
                    verb = "Transcrevendo" if status == "transcribing" else "Analisando"
                    self._prog_var.set(pct)
                    self._ep_label.config(
                        text=f"{verb}: {cur}/{total} — {name}", fg=self.TEXT
                    )
                elif kind == "stage":
                    _, stage_name, start_ts = msg
                    self._stage_name  = stage_name
                    self._stage_start = start_ts
                elif kind == "step_progress":
                    _, pct, label = msg
                    self._step_prog_var.set(pct)
                    if label is not None:
                        self._step_label.config(text=label)
                elif kind == "done":
                    self._on_done()
        except queue.Empty:
            pass

        if self._stage_start is not None:
            elapsed = time.time() - self._stage_start
            self._stage_label.config(
                text=f"Etapa: {self._stage_name} — decorrido: {format_duration(elapsed)}"
            )
        self.after(100, self._poll)

    def _progress_cb(self, cur, total, name, status):
        self._queue.put(("progress", cur, total, name, status))

    def _stage_cb(self, stage_name):
        self._queue.put(("stage", stage_name, time.time()))

    def _step_cb(self, pct, label=""):
        self._queue.put(("step_progress", pct, label))

    # ── Iniciar / Parar ──────────────────────────────────────────

    def _start(self):
        # Validação básica
        if not cfg("videos_dir"):
            messagebox.showerror(
                "Configuração incompleta",
                "Configure a pasta dos vídeos antes de iniciar.\n"
                "Clique em ⚙ Configurações."
            )
            return
        if not cfg("pasta_transcricoes"):
            messagebox.showerror(
                "Configuração incompleta",
                "Configure as pastas de saída antes de iniciar.\n"
                "Clique em ⚙ Configurações."
            )
            return

        if self._running:
            return
        self._running     = True
        self._stop.clear()
        self._stage_name  = ""
        self._stage_start = None
        self._btn_start.config(state=tk.DISABLED, bg=self.GRAY)
        self._btn_stop.config(state=tk.NORMAL, bg=self.RED)
        self._write_log("\n", "normal")

        logger = Logger(self._queue)
        logger.open()
        phase  = self._phase.get()

        def worker():
            run_pipeline(
                phase, logger,
                self._progress_cb, self._stage_cb, self._step_cb,
                self._stop
            )
            logger.close()
            self._queue.put(("done",))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_click(self):
        self._stop.set()
        self._btn_stop.config(state=tk.DISABLED, bg=self.GRAY)
        self._write_log("[sistema] Sinal de parada enviado...\n", "warn")

    def _on_done(self):
        self._running     = False
        self._stage_start = None
        self._stage_label.config(text="")
        self._step_label.config(text="")
        self._step_prog_var.set(0)
        self._btn_start.config(state=tk.NORMAL, bg=self.ACCENT)
        self._btn_stop.config(state=tk.DISABLED, bg=self.GRAY)
        self._ep_label.config(
            text=f"Concluído — relatório em: {cfg('arquivo_log')}",
            fg=self.GREEN
        )
        self._prog_var.set(100)


# ─── Entrada ─────────────────────────────────────────────────────

if __name__ == "__main__":
    load_config()
    App().mainloop()

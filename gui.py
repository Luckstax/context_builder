"""
gui.py — Interface Tkinter: janela principal (App) e janela de
Configurações (SettingsWindow). Nenhuma lógica de negócio mora aqui;
tudo é delegado para pipeline.run_pipeline().
"""

import queue
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
from utils import format_duration
from logger import Logger
from pipeline import run_pipeline


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
        self._field(frame, "lms_context_length", "Contexto do modelo (tokens)")
        self._field(frame, "chunk_minutes", "Minutos de transcrição por chunk")

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
            var.set(str(config.cfg(key)))

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
            # Converte campos numéricos
            if key in ("lms_port", "lms_context_length", "chunk_minutes"):
                try:
                    val = int(val)
                except ValueError:
                    messagebox.showerror("Erro", f"'{key}' deve ser um número.")
                    return
            config.set_cfg(key, val)
        config.save_config()
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

        self._series_label = tk.Label(self, text=config.cfg("series_name"),
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
            lbl = tk.Label(pf, text=config.cfg(key) or "(não configurado)",
                           bg=self.PANEL,
                           fg=self.TEXT if config.cfg(key) else self.RED,
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
        self._series_label.config(text=config.cfg("series_name"))
        for _, key in self._path_rows:
            val = config.cfg(key)
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
        if not config.cfg("videos_dir"):
            messagebox.showerror(
                "Configuração incompleta",
                "Configure a pasta dos vídeos antes de iniciar.\n"
                "Clique em ⚙ Configurações."
            )
            return
        if not config.cfg("pasta_transcricoes"):
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
            text=f"Concluído — relatório em: {config.cfg('arquivo_log')}",
            fg=self.GREEN
        )
        self._prog_var.set(100)

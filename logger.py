"""
logger.py — Logger de execução: grava em arquivo, manda linhas pra fila
da GUI e acumula estatísticas para o relatório final.
"""

import queue
from datetime import datetime
from pathlib import Path

import config
from utils import format_duration


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
        log_path = Path(config.cfg("arquivo_log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(log_path, "w", encoding="utf-8")
        self.t0 = datetime.now()
        self._raw(
            f"LOG DE EXECUÇÃO\n"
            f"Data: {self.t0:%d/%m/%Y %H:%M:%S}\n"
            f"Série: {config.cfg('series_name')}\n\n"
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

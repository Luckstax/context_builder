"""
config.py — Configuração do Context Builder
============================================
Carrega/salva config.json e expõe funções de leitura (cfg) e
escrita (set_cfg) para o resto do programa.

IMPORTANTE: os outros módulos devem fazer `import config` e acessar
`config.cfg(...)`, nunca `from config import CONFIG` — load_config()
reatribui o dicionário CONFIG inteiro (não apenas muta ele), então um
import direto do nome CONFIG feito antes de load_config() rodar ficaria
apontando pro dicionário vazio antigo.
"""

import json
from pathlib import Path

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
    "lms_model_id":       "qwen2.5-7b-instruct",
    "lms_model_quant":    "",
    "lms_port":           1234,
    "lms_context_length": 8192,
    "chunk_minutes":      8,
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


def set_cfg(key: str, value):
    CONFIG[key] = value


def lms_base_url() -> str:
    return f"http://localhost:{cfg('lms_port')}/v1"

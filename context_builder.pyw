"""
context_builder.pyw — ponto de entrada
=======================================
Pipeline de contexto para séries animadas: transcreve episódios
dublados e gera context.json estruturado para uso em legendagem
assistida por IA.

Este arquivo só carrega a configuração e abre a janela principal.
A lógica está dividida em módulos por responsabilidade:

    config.py       — leitura/escrita de config.json
    utils.py        — utilitários genéricos (format_duration)
    logger.py        — Logger (log em tela + arquivo + estatísticas)
    audio.py         — extração de áudio via PyAV
    lms_client.py    — integração com LM Studio
    transcricao.py   — Fase 1 (Whisper)
    analise.py        — Fase 2 (LM Studio) e consolidação final
    pipeline.py       — orquestra as duas fases
    gui.py            — janela principal e janela de Configurações

INSTALAÇÃO:
    pip install openai faster-whisper
    pip install torch --index-url https://download.pytorch.org/whl/cu121
    (tkinter já vem com Python)
"""

import config
from gui import App

if __name__ == "__main__":
    config.load_config()
    App().mainloop()

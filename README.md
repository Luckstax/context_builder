# 🐞 Context Builder

Pipeline para geração de contexto estruturado a partir de episódios de séries animadas dubladas, com interface gráfica. Desenvolvido para auxiliar na criação de legendas localizadas usando IA.

## O que faz

Processa vídeos de uma série em duas fases:

**Fase 1 — Transcrição:** extrai o áudio de cada episódio e transcreve a fala usando o modelo Whisper (local, sem API externa).

**Fase 2 — Análise:** lê cada transcrição com um modelo de linguagem local (via LM Studio) e extrai informações estruturadas — personagens, eventos, glossário de termos que não devem ser traduzidos (como nomes próprios), locais e regras de legendagem.

O resultado final é um arquivo `context.json` que serve como "memória" da série para uso posterior na geração de legendas localizadas.

## Requisitos

- Python 3.10 ou superior
- [LM Studio](https://lmstudio.ai) instalado (gratuito)
- Windows, Linux ou macOS

GPU NVIDIA é opcional — o programa roda em CPU com quantização int8, porém mais lentamente.

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/seu-usuario/context-builder.git
cd context-builder

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Instale o PyTorch com suporte CUDA (opcional, para GPU NVIDIA)
pip install torch --index-url https://download.pytorch.org/whl/cu121
# Se preferir rodar só em CPU:
pip install torch
```

## Configuração

Na primeira execução, clique em **⚙ Configurações** dentro do programa e preencha:

| Campo | Descrição |
|---|---|
| Nome da série | Nome usado nos prompts e no context.json |
| Idioma do áudio | Código ISO do idioma (pt, fr, es, en…) |
| Pasta dos vídeos | Onde estão os arquivos .mp4/.mkv dos episódios |
| Pasta de saída base | Clique em "Definir pasta de saída base" para preencher tudo de uma vez |
| Modelo Whisper | `medium` é o melhor equilíbrio qualidade/velocidade |
| Dispositivo Whisper | `cpu` para qualquer máquina, `cuda` se tiver GPU NVIDIA |
| Modelo LM Studio | ID do modelo carregado no LM Studio |

As configurações são salvas em `config.json` na mesma pasta do programa.

## Uso

```
1. Abra o LM Studio e baixe um modelo compatível
   (recomendado: unsloth/Phi-4-mini-instruct-GGUF @ Q4_K_M, ~2.5 GB)

2. Execute o programa:
   python context_builder.py

3. Configure as pastas em ⚙ Configurações

4. Escolha a fase:
   - Fase 1: apenas transcrição (Whisper)
   - Fase 2: apenas análise (LM Studio — requer transcrições prontas)
   - Ambas: executa tudo em sequência

5. Clique em INICIAR
```

O LM Studio é iniciado automaticamente pelo programa quando necessário.

## Cache e retomada

Cada episódio gera arquivos intermediários em cache:
- `transcricoes/EPISODIO.txt` — saída da Fase 1
- `analises/EPISODIO.json` — saída da Fase 2

Se o processamento for interrompido, o programa retoma automaticamente do ponto onde parou na próxima execução, sem reprocessar episódios já concluídos.

## Estrutura de saída

```
pasta-de-saída/
  ├── transcricoes/      ← .txt por episódio (Fase 1)
  ├── analises/          ← .json por episódio (Fase 2)
  ├── context.json       ← arquivo final consolidado
  └── relatorio.txt      ← log da última execução
```

## Requisitos de hardware

| Componente | Mínimo | Recomendado |
|---|---|---|
| RAM | 8 GB | 16 GB |
| GPU VRAM | — (CPU) | 4 GB+ |
| Armazenamento | 5 GB livres | 10 GB+ |

## Dependências

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — transcrição de fala
- [openai](https://github.com/openai/openai-python) — cliente compatível com LM Studio
- [PyAV](https://github.com/PyAV-Org/PyAV) — extração de áudio (instalado automaticamente com faster-whisper)
- tkinter — interface gráfica (incluído no Python)

## Licença

MIT

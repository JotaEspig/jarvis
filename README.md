# Jarvis

Assistente de desenvolvimento por **voz** que atua como **ponte** para o Claude.

Você conversa por voz com o Jarvis (modelo barato, Haiku 4.5). Ele discute, faz perguntas e, quando
você encerra, gera um prompt bem formulado e escolhe o modelo/esforço do **worker** conforme a
complexidade. O worker é um coding agent real (Sonnet 5 ou Opus 4.8) que age no seu repositório e
herda skills/subagents/MCP/CLAUDE.md locais e globais. Se o worker precisa de algo (uma pergunta ou
aprovação para uma ação arriscada), o Jarvis repassa por voz. As entregas chegam como **resumo por
voz + detalhe na tela**.

## Como funciona

```
Você (voz) ⇄ Jarvis (Haiku)  ── gera prompt + escolhe modelo/effort ──▶  Worker (Sonnet 5 / Opus 4.8)
              ▲   tradutor/ponte                                            │ age no repo, usa skills/agents
              └───────── pergunta / aprovação (por voz) ───────────────────┘
```

Componentes: FastAPI + WebSocket (UI web local, push-to-talk) · STT `faster-whisper` · TTS `Piper`
(voz pt-BR) · Jarvis via SDK `anthropic` · worker via **Claude Agent SDK**.

## Instalação para a equipe (Docker — recomendado)

Modelos de voz já vêm embutidos na imagem; só precisa de `ANTHROPIC_API_KEY`.

```bash
git clone git@github.com:JotaEspig/jarvis.git && cd jarvis
cp .env.example .env      # preencha ANTHROPIC_API_KEY e HOST_TARGET_REPO
docker compose up --build
```

Abra http://localhost:8765. O `HOST_TARGET_REPO` do seu `.env` é montado em `/workspace` (onde o
worker atua); seu `~/.claude` é montado read-only para herdar skills/agents globais. O microfone é
acessado pelo navegador, então funciona em qualquer SO.

## Instalação local (uv)

```bash
git clone git@github.com:JotaEspig/jarvis.git && cd jarvis
./install.sh                              # cria .venv, instala e baixa os modelos de voz
# edite o .env (ANTHROPIC_API_KEY, JARVIS_TARGET_REPO)
source .venv/bin/activate && jarvis       # sobe o servidor e abre o navegador
```

Requisitos: Python 3.12, `uv` e `ANTHROPIC_API_KEY`.

## Uso

1. Segure o botão do microfone e fale (ou digite) o que precisa.
2. O Jarvis pergunta e refina; quando terminar, clique em **Encerrar conversa**.
3. O worker roda no seu repositório; acompanhe o detalhe na tela e o resumo por voz.
4. Se aparecer uma pergunta, responda por voz/texto; se pedir autorização, use **Autorizar/Negar**.

## Configuração

Tudo por variáveis de ambiente (prefixo `JARVIS_`) — veja `.env.example` e `jarvis/config.py`.
Modelos: `JARVIS_JARVIS_MODEL` (Haiku), `JARVIS_WORKER_MODEL_SIMPLE` (Sonnet 5),
`JARVIS_WORKER_MODEL_COMPLEX` (Opus 4.8). Voz: `JARVIS_WHISPER_MODEL`, `JARVIS_PIPER_VOICE`.

## Testes

```bash
pip install -e ".[dev]" && pytest       # testes rápidos (o round-trip de voz é marcado `slow`)
```

## Troubleshooting

- **Sem áudio / microfone:** o navegador exige contexto seguro; `http://localhost` e `127.0.0.1`
  contam como seguros. Autorize o microfone quando o navegador pedir.
- **STT/TTS "indisponível":** os modelos não baixaram — rode `python -m jarvis.download` (ou rebuild
  da imagem). A app continua funcionando por texto/tela enquanto isso.

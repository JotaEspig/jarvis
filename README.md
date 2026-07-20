# Jarvis

Assistente de desenvolvimento por **voz** que atua como **ponte** para o Claude.

Você conversa por voz com o Jarvis (modelo barato, Haiku 4.5). Ele discute, faz perguntas e, quando
você encerra, gera um prompt bem formulado e aciona um **worker** — um coding agent real (Sonnet 5
ou Opus 4.8, conforme a complexidade) que age no seu repositório e herda skills/subagents/MCP/CLAUDE.md
locais e globais. Se o worker precisa de algo, o Jarvis repassa por voz. As entregas chegam como
resumo por voz + detalhe na tela.

> 🚧 Em desenvolvimento. O esqueleto (servidor web + WebSocket + UI) já roda; voz, cérebro e worker
> estão sendo adicionados por fases (veja o plano do projeto).

## Rodar (desenvolvimento)

```bash
cp .env.example .env   # preencha ANTHROPIC_API_KEY e JARVIS_TARGET_REPO
pip install -e .        # ou: uv pip install -e .
jarvis                  # sobe o servidor e abre o navegador
```

Requisitos: Python 3.10+ e `ANTHROPIC_API_KEY`. O microfone é acessado pelo navegador (push-to-talk).

Instalação para a equipe (Docker + `install.sh`) chega na fase de distribuição.

# Jarvis

Assistente pessoal por voz, no estilo do JARVIS do Homem de Ferro. Você o chama pela wake-word e
conversa naturalmente: ele entende o que você quer, responde em no máximo 3 frases e delega a
execução (codar, mexer em arquivos, pesquisar na web, usar seus MCPs, abrir apps) para agentes
Claude mais fortes, que trabalham em background e avisam quando terminam.

- **Sem custo extra:** usa só a sua assinatura do Claude (token do `claude setup-token`), e toda a
  voz roda localmente (wake-word, VAD, STT e TTS).
- **Funciona no Linux e no WSL:** no WSL, o áudio roda nativo no Windows.

## Arquitetura

```
EDGE (Linux, ou Windows quando o core está no WSL)
  mic → AEC (WebRTC) → wake-word (openWakeWord) → VAD (Silero) ──┐    playback + barge-in
                                                                 │    bandeja · notificações
                         WebSocket local (eventos + PCM) ⇅ ──────┘
CORE (Linux / WSL)
  STT Parakeet TDT v3 (sherpa-onnx), com decodificação por trechos
   → endereçamento (a fala é para mim?)
   → FRONT: Claude Haiku, sessão persistente: lapida a fala, conversa curta, decide agir
       → TTS Kokoro, frase a frase, em streaming
       → BACK: Claude Sonnet (padrão) / Opus (complexo), várias tarefas em paralelo:
               plano → validação → execução → resumo falado
               ações arriscadas pedem aprovação por voz
```

| Etapa | Implementação | Latência típica (i5 4 threads, sem GPU) |
|---|---|---|
| Fim da fala | VAD com 600 ms de silêncio | 0,6 s |
| STT | Parakeet v3 int8, só o último trecho | ~0,25 s |
| Haiku | sessão SDK aquecida, streaming | ~0,9–1,3 s até a 1ª frase |
| TTS | Kokoro int8, 1ª frase cortada na vírgula | ~0,4–0,8 s |

Por que cada escolha:

- **Agent SDK + sessão persistente:** o token de assinatura só funciona pelo CLI do Claude Code.
  Manter a sessão aberta tira o custo de subir o processo a cada fala.
- **Parakeet em vez de Whisper:** o Whisper sempre processa uma janela de 30 s (2,7 s por fala no
  `small`); o Parakeet leva ~0,15 s por segundo de áudio e lida bem com termos técnicos em inglês.
- **Edge separado:** no WSL o áudio pelo WSLg tem latência alta e não tem AEC, então o edge roda nativo no Windows.

## Pré-requisitos

- Assinatura do Claude e o [Claude Code](https://claude.com/claude-code) instalado (o SDK usa o CLI).
- Token gerado com `claude setup-token`, guardado em `~/.config/jarvis/token` ou exportado em
  `CLAUDE_CODE_OAUTH_TOKEN`. Se existir uma `ANTHROPIC_API_KEY` no ambiente, o Jarvis a **remove**
  dos processos dele, para nunca cobrar via API.
- [uv](https://docs.astral.sh/uv/) (o Python 3.11 é baixado por ele).
- ~2 GB livres de RAM para os modelos de voz (o Parakeet sozinho ocupa ~1 GB).

## Instalação

### Linux (core + edge)

```bash
./deploy/install-linux.sh        # venv, modelos, token, config e serviço systemd de usuário
```

Para ter o ícone na bandeja no Wayland (Hyprland/waybar etc.), o instalador inclui o extra `tray`
quando encontra a `libayatana-appindicator`.

### WSL (core no WSL, edge no Windows)

1. No WSL, com systemd habilitado (`[boot] systemd=true` em `/etc/wsl.conf`): `./deploy/install-linux.sh`
   (ele detecta o WSL e instala só o core).
2. No Windows (PowerShell): `powershell -ExecutionPolicy Bypass -File deploy\install-windows-edge.ps1`
   (instala o `jarvis[edge]` via uv a partir do repo no WSL e o coloca na inicialização).

O edge conecta em `ws://127.0.0.1:8766`; o `localhost` do Windows alcança o WSL2. No WSL, as
ações de desktop do worker (abrir arquivos, pastas, URLs e apps) agem no Windows via `powershell.exe`.

### Desenvolvimento

```bash
uv venv --python 3.11 && uv pip install -e ".[core,edge,dev]"
jarvis doctor                # credencial, latência do Haiku, modelos, áudio
jarvis edge --loopback       # testa mic/VAD/AEC sem o core: fale e ouça de volta
jarvis say "Olá, senhor."    # testa o TTS
jarvis run                   # core + edge
pytest
```

## Uso

1. Diga a wake-word (padrão: **"hey jarvis"**, pronunciado em inglês). Toca um bip e a conversa abre.
2. Fale normalmente; depois da wake-word não precisa repeti-la enquanto a conversa estiver aberta.
   Falas que não parecem dirigidas a ele (conversa com outra pessoa, telefone) são ignoradas.
3. Ele confirma em uma frase e despacha a tarefa. Você pode seguir conversando ou pedir outras
   tarefas em paralelo.
4. Diga "valeu, só isso" para encerrar; a conversa também fecha após ~10 s de silêncio. As
   tarefas continuam em background.
5. Quando uma tarefa termina, ou precisa de uma resposta ou aprovação sua, ele toca um bip, fala
   e manda uma notificação. A conversa reabre por alguns segundos, sem precisar da wake-word.
6. Interrompa a qualquer momento falando por cima dele (barge-in).

Para citar projetos por voz ("no projeto jarvis…"): todo repo git em `projects_root` (padrão
`~/dev`) já vale. Para apelidos, use `jarvis projects add portal ~/work/portal-do-cliente`.

### Wake-word própria

Veja [tools/wakeword/README.md](tools/wakeword/README.md): treino gratuito no Colab e calibração
com `jarvis wakeword`.

## Configuração

`~/.config/jarvis/config.toml`. Veja [config.example.toml](config.example.toml) para todas as
opções: voz e velocidade do TTS (`kokoro`/`piper`), tempos de silêncio, janela de conversa,
modelos do front e do back, projetos.

As instruções dos agentes ficam em [jarvis/prompts/front.md](jarvis/prompts/front.md) (persona,
regra das 3 frases, quando despachar) e [jarvis/prompts/back.md](jarvis/prompts/back.md)
(protocolo plano → validação → execução → `RESUMO_FALADO`). O back também herda suas skills,
MCPs e CLAUDE.md do Claude Code.

## Segurança

O back pode agir sozinho em leitura, em edição de arquivos **dentro do projeto** e em comandos comuns. Pedem
aprovação por voz: apagar arquivos, `git push`/`reset --hard`/`rebase`, `sudo`, requisições HTTP
de escrita, execução no Windows, escrita fora do projeto e tools de MCP que enviam, criam ou apagam
coisas (e-mail, Teams etc.). Regras em [jarvis/core/risk.py](jarvis/core/risk.py).

# Wake-word própria (openWakeWord)

O Jarvis vem com o modelo pré-treinado `hey_jarvis`, que funciona com pronúncia em **inglês**
("hei djárvis"). Para outra frase, ou para a pronúncia em pt-BR, treine um modelo próprio. O
treino usa amostras **sintéticas** (TTS com centenas de vozes), então você não precisa gravar nada.

## 1. Treinar (Google Colab gratuito, ~1 h)

Na sua CPU sem GPU o treino levaria muitas horas, por isso o caminho recomendado é o Colab:

1. Abra o notebook oficial *automatic model training* do openWakeWord:
   https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
   (botão "Open in Colab"; em *Runtime → Change runtime type*, escolha **T4 GPU**, que é grátis).
2. Em `target_word`, escreva a frase **como ela soa em inglês**, porque o gerador de amostras é
   inglês. Exemplos:
   - "ei jarvis" → `ay jarvis`
   - "sexta-feira" → `sesh ta fay rah`
   - "ok computador" → `okay compu tador`
   Prefira frases com 3 ou 4 sílabas e sons distintos: frases curtas demais disparam à toa.
3. Rode todas as células. No final, baixe o `.onnx` gerado (ex.: `ay_jarvis.onnx`).

## 2. Instalar

```bash
mkdir -p ~/.config/jarvis/wakeword
mv ~/Downloads/ay_jarvis.onnx ~/.config/jarvis/wakeword/
```

Em `~/.config/jarvis/config.toml`:

```toml
[wakeword]
model = "~/.config/jarvis/wakeword/ay_jarvis.onnx"
threshold = 0.5
```

No WSL, o arquivo e o config ficam **no Windows**, onde o edge roda:
`%USERPROFILE%\.config\jarvis\`.

## 3. Calibrar

```bash
jarvis wakeword --model ~/.config/jarvis/wakeword/ay_jarvis.onnx
```

Fale a frase algumas vezes, de perto e de longe, e converse normalmente perto do mic. Ajuste o
`threshold` para ficar acima dos scores da conversa normal e abaixo dos da frase (tipicamente
entre 0,4 e 0,7).

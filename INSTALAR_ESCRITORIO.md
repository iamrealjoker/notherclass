# Instalación en tu escritorio (sin Docker)

Para Linux/macOS. En Windows funciona igual con **WSL2** (recomendado) o Git Bash
+ Python nativo.

---

## 1. Requisitos

| Cosa | Versión | Notas |
|---|---|---|
| **Python** | 3.10+ | `python3 --version` |
| **pip** | — | `python3 -m pip --version` |
| **ffmpeg** | — | Solo para **voz** (convertir audio) |
| **git** | — | Para clonar |

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install -y python3 python3-pip ffmpeg git

# macOS (Homebrew)
brew install python ffmpeg git
```

---

## 2. Clona el proyecto

```bash
git clone https://github.com/<tu-usuario>/notherclass.git
cd notherclass
```

---

## 3. Configura tus claves

```bash
cp agent/.env.example agent/.env
```

Abre `agent/.env` y rellena **lo mínimo**:

```ini
# Identidad
TW_AGENT_NAME=Joker
TW_AGENT_STORE=Forja

# Tu LLM (cualquier API compatible con OpenAI)
TW_LLM_PROVIDER=deepseek
TW_LLM_API_KEY=aqui_tu_clave
TW_LLM_MODEL=deepseek-flash

# Telegram
TW_TELEGRAM_BOT_TOKEN=aqui_tu_token_de_botfather
TW_TELEGRAM_ALLOWED_USER_IDS=aqui_tu_id_numerico
```

> El resto de variables tienen valores por defecto razonables. Están todas
> explicadas en el propio fichero `.env.example`.

---

## 4. Instala las dependencias

```bash
pip install -r agent/requirements.txt
pip install -r consola/requirements.txt
```

> Es rápido: casi todo usa la librería estándar de Python.

---

## 5. Voz local (opcional, pero es el gran ahorro)

El motor de voz corre en tu máquina, así que **no gasta tokens**.

**a) Modelo de transcripción (STT)** — dos opciones:

```bash
# Opción rápida: faster-whisper en un venv aparte (recomendado)
python3.11 -m venv agent/venv311
agent/venv311/bin/pip install faster-whisper
```

Con eso el agente usa el **servicio residente** (el modelo se carga una vez y
responde en ~1-2 s por nota).

**b) Voz de salida (TTS)** — `piper`:

```bash
# Descarga piper (binario) y una voz en español
mkdir -p ~/piper && cd ~/piper
# Descarga desde https://github.com/rhasspy/piper/releases
# y los modelos .onnx desde https://huggingface.co/rhasspy/piper-voices
```

Luego, en `agent/.env`:

```ini
TW_PIPER_BIN=/home/tu-usuario/piper/piper
TW_PIPER_MODEL=/ruta/a/es_ES-voz-medium.onnx
TW_WHISPER_MODEL=/ruta/a/ggml-base.bin   # si usas whisper.cpp en lugar de faster-whisper
```

---

## 6. Arranca el agente

```bash
cd agent
python3 run.py                     # se queda escuchando Telegram
```

Otros modos útiles:

```bash
python3 run.py --list-agents        # ver qué agentes hay configurados
python3 run.py --oneshot "hola"     # un turno por consola (sin Telegram)
python3 run.py --agent jokerv1      # arrancar un agente concreto
```

---

## 7. Arranca la consola web

En **otra terminal**:

```bash
cd consola
python3 -u app.py                  # http://localhost:5000
```

Abre **http://localhost:5000** en el navegador. Ahí tienes el chat, la memoria,
las pizarras y el despliegue de "⚙️ ejecutado".

---

## 8. Que se arranque solo (opcional)

**Linux — servicio de usuario (systemd)**

`~/.config/systemd/user/notherclass.service`:

```ini
[Unit]
Description=NotherClass Agent
After=network.target

[Service]
WorkingDirectory=%h/notherclass/agent
ExecStart=/usr/bin/python3 run.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now notherclass
systemctl --user status notherclass
```

**macOS — `launchd`**: crea un `plist` análogo en `~/Library/LaunchAgents/`.

---

## 9. Problemas frecuentes

| Síntoma | Qué pasa | Solución |
|---|---|---|
| `No hay TW_TELEGRAM_BOT_TOKEN` | Falta el token | Rellena `agent/.env` |
| `409 Conflict` | **Dos procesos con el mismo token** | Cierra el duplicado; cada bot = un token |
| El bot no responde en grupo | Modo privacidad activo | @BotFather → `/setprivacy` → **Disable** |
| `ModuleNotFoundError` | Faltan dependencias | `pip install -r consola/requirements.txt` |
| El micro no funciona | La web necesita contexto seguro | Usa `localhost` o HTTPS |
| Transcripción lenta (~5 s) | El modelo se recarga cada vez | Levanta el venv311 (paso 5a) |

---

➡️ Siguiente: **[`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)** (modo grupo + revisión automática)

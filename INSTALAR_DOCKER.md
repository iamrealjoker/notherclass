# Instalación con Docker

La vía más cómoda: **no instalas Python, ni ffmpeg, ni whisper, ni piper**.
Todo viene dentro de la imagen.

---

## 1. Requisitos

- **Docker** ≥ 24 y el plugin **Compose** (`docker compose version`)
- **git**
- ~**4 GB** libres en disco (la imagen trae los motores de voz)

```bash
# Comprueba que lo tienes
docker --version && docker compose version
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
cp .env.compose.example .env
```

En **`agent/.env`** rellena lo mínimo:

```ini
TW_AGENT_NAME=Joker
TW_AGENT_STORE=Forja

TW_LLM_PROVIDER=deepseek
TW_LLM_API_KEY=aqui_tu_clave
TW_LLM_MODEL=deepseek-chat

TW_TELEGRAM_BOT_TOKEN=aqui_tu_token_de_botfather
TW_TELEGRAM_ALLOWED_USER_IDS=aqui_tu_id_numerico
```

En **`.env`** (el de la raíz) solo ajustas infraestructura:

```ini
TW_PORT_HOST=5000     # puerto donde publicar la consola
TZ=Europe/Madrid
# opcional: dónde guardar los datos persistentes (memoria, voces, consola)
# TW_DATA=./data
```

> ⚠️ **Rellena `TW_TELEGRAM_ALLOWED_USER_IDS`** con tu id numérico (lo da
> [@userinfobot](https://t.me/userinfobot)). Si lo dejas vacío, **cualquiera que
> encuentre tu bot podrá hablar con tu agente** y gastar tus tokens.
>
> 🔒 Los dos `.env` están en `.gitignore`: **nunca** se suben al repositorio.

---

## 4. Construye y levanta

```bash
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

La primera vez tarda un rato (compila whisper y descarga los modelos).
Las siguientes arranca en segundos.

---

## 4-bis. Voz (STT + TTS): 0 tokens, todo local

La imagen ya trae los motores (**whisper.cpp** y **piper**), pero **las voces
`.onnx` (~60 MB) no se meten en la imagen** (las harían engordar y cambian poco).
Se **descargan solas** la primera vez que el agente tiene que hablar:

```
[audio] voz 'es_ES-davefx-medium.onnx' no está → descargando (~60 MB, solo esta vez)…
[audio] ✅ voz lista.
```

Quedan en `agent/audio_models/`. Para **no volver a descargarlas** en cada
rebuild, móntalas como volumen (añade a tu `docker-compose.dev.yml`):

```yaml
    volumes:
      - ${TW_CODE:-./dev/notherclass}:/app/notherclass
      - ./dev/notherclass/agent/audio_models:/app/notherclass/agent/audio_models
```

> Sin internet en el primer arranque el TTS avisa
> (`⚠️ no pude descargar la voz`) y el agente sigue funcionando **solo con texto**.

---

## 5. Comprueba que está vivo

```bash
# ¿Está corriendo y sano?
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml ps

# La consola responde
curl -s http://localhost:5000/health
#   -> {"ok":true,"servicio":"consola-agentes"}

# ¿Qué está pasando dentro?
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml logs -f
```

Abre **http://localhost:5000** y ya lo tienes.

---

## 6. Trabajar con él

```bash
# Ver los agentes configurados
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml \
  exec notherclass python3 run.py --list-agents

# Un turno de prueba, sin Telegram
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml \
  exec notherclass python3 run.py --oneshot "hola, preséntate"

# Entrar en el contenedor
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml exec notherclass bash

# Parar / arrancar
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml down
docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml up -d
```

---

## 7. Dónde viven tus datos

| Qué | Dónde (en tu host) |
|---|---|
| Memoria del agente | `data/MEMORY/brain_<store>.sqlite3` |
| Voces (TTS) | `data/audio_models/` |
| Consola (historial, voz) | `data/consola/` |
| Config y secretos | `agent/.env` |
| Pizarra del grupo | `grupo_bus/` |

Los tres primeros van a `./data/` (o donde pongas `TW_DATA` en tu `.env`), **fuera
del código**: sobreviven a `down`, a reconstruir la imagen y a actualizarla.

**Haz backup copiando `data/`** y tienes la memoria de tu agente a salvo.

---

## 8. Publicarlo en Internet (leer antes)

La consola **puede ejecutar comandos en tu máquina**. Por diseño viene atada a
**`127.0.0.1`** (solo local). Si quieres acceso desde fuera:

1. **Nunca** publiques la consola directamente a Internet sin autenticación.
2. Pon delante un **proxy inverso con HTTPS + autenticación** (Caddy/nginx con
   Basic Auth, OAuth, mTLS…).
3. Alternativa más simple: **VPN** (WireGuard/Tailscale) o un túnel **con
   autenticación** (p. ej. Cloudflare Access).
4. En el servidor: cortafuegos (`ufw`), `fail2ban` y HTTPS con Let's Encrypt.

---

## 9. Problemas frecuentes

| Síntoma | Causa habitual | Solución |
|---|---|---|
| `docker compose` no existe | Falta el plugin Compose | Instala `docker-compose-plugin` |
| `env file ... not found` | Falta `agent/.env` | `cp agent/.env.example agent/.env` |
| El contenedor se reinicia en bucle | Error de arranque | `docker compose ... logs -f` |
| `409 Conflict` en Telegram | **Mismo token en dos sitios** | Un token por bot; cierra el duplicado |
| La consola no responde en 5000 | Puerto ocupado | Cambia `TW_PORT_HOST` en `.env` |
| Voz lenta | STT recargando el modelo | Revisa los logs del servicio residente |
| Permiso denegado en los ficheros | UID distinto | Ajusta permisos de `agent/MEMORY` |

---

➡️ Siguiente: **[`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)** (modo grupo + revisión automática)

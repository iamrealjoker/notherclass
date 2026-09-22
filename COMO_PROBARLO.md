# Cómo probar NotherClass

Dos formas de levantarlo. Elige **una**:

- **🖥️ En tu escritorio (sin Docker)** → [`INSTALAR_ESCRITORIO.md`](INSTALAR_ESCRITORIO.md)
- **🐳 Con Docker** → [`INSTALAR_DOCKER.md`](INSTALAR_DOCKER.md) *(la más cómoda)*

En los dos casos acabas con lo mismo: **el agente escuchando Telegram** y la
**consola web** en `http://localhost:5000`.

---

## ✅ Qué necesitas antes (igual para las dos)

### 1. Una clave de un LLM
Cualquier proveedor **compatible con la API de OpenAI** (DeepSeek, OpenAI,
OpenRouter, Z.AI…). Consigue tu clave y tenla a mano.

### 2. Un bot de Telegram (opcional pero recomendado)
1. Abre Telegram y habla con **@BotFather**.
2. `/newbot` → ponle nombre → ponle usuario → te da un **token** tipo
   `123456789:AAF-xxxxxxxxxxxxxxxxxxxxxxxxx`.
3. **Guárdalo.** Es lo que permite hablar con tu agente desde el móvil.

> ¿Prefieres probar sin Telegram? Se puede: `python run.py --oneshot "hola"`.
> Pero la gracia está en hablarle desde el móvil.

### 3. Tu ID de Telegram (recomendado por seguridad)
Habla con **@userinfobot** y te dice tu **ID numérico**. Así solo TÚ puedes
usar el bot (si lo dejas vacío, responde a cualquiera que conozca su @usuario).

---

## 🧪 Cómo saber si ha funcionado

Tres pruebas rápidas, en orden:

```bash
# 1) El agente responde a un mensaje (sin Telegram)
cd agent && python run.py --oneshot "hola, preséntate"

# 2) Lista los agentes configurados
python run.py --list-agents

# 3) La consola web responde
curl -s http://localhost:5000/health
#    -> {"ok":true,"servicio":"consola-agentes"}
```

Y la prueba definitiva: **mándale un mensaje de voz por Telegram** y verás
cómo lo transcribe, piensa y te contesta (con voz, si lo tienes activado).

---

## 🐛 Historial de correcciones (v1.0)

Bugs reales encontrados probando una instalación desde cero, y su arreglo:

| Bug | Qué pasaba | Arreglo |
|---|---|---|
| **Las claves del `.env` se ignoraban** | `configAgentes.json` pedía `$keys.deepseek_primary` → buscaba una variable inexistente → clave vacía → **HTTP 401** | `_defaults` ahora lee `$env.TW_LLM_API_KEY` / `_BASE_URL` / `_MODEL` de tu `.env` |
| **Modelo inexistente** | `.env.example` ponía `deepseek-v4-flash` (no existe) | `deepseek-chat` (documentado también `deepseek-reasoner`) |
| **Fallback que pisaba tu clave** | Si faltaba la clave, se usaba `DEEPSEEK_API_KEY` del entorno (a veces caducada) y **daba 401** | El fallback solo actúa si NO definiste `TW_LLM_API_KEY` |
| **Agentes de ejemplo arrancaban solos** | `jokerv1`/`jokerv2` usaban el proveedor `glm` (clave que no tienes) y el supervisor los lanzaba → **401 en bucle** | Vienen con `"_supervisar": false`; los activas cuando quieras |
| **El coordinador arrancaba sin grupo** | Proceso de más sin sentido si no usas grupos | También `_supervisar: false` |
| **Un agente nuevo arrancaba al instante** | Al crearlo con `crear_agente`, el supervisor lo lanzaba **2 s después** e intentaba llamar al LLM → 401 | Los agentes nuevos nacen con `_supervisar: false` |
| **El agente no te entrevistaba** | Pedías "crea un agente" y lo creaba a ciegas, inventándose nombre, rol y token | Su descripción y la persona de Joker obligan a **pedir 6 datos** (nombre, rol, tono, valores, token, contexto) antes de crear nada |
| **El TTS no tenía voz (Docker/limpio)** | Las voces `.onnx` de piper **no están en el repo** ni en la imagen, así que en instalación limpia el TTS fallaba al hablar | `core/audio.py` **descarga la voz sola** (~60 MB, una vez) y la cachea en `agent/audio_models/` |
| **Dos bots con el mismo token** | Dos procesos usando el mismo `TW_TELEGRAM_BOT_TOKEN`: Telegram solo entrega a uno → el otro parece "muerto" y el mensaje acaba en error | **Un token = un bot = un proceso.** Para probar, para el viejo antes de arrancar el nuevo |

---

## 🧩 Qué arranca y qué no (importante)

Por defecto **solo arranca `joker`** (y su consola web). Los **agentes de ejemplo**
y el **coordinador del grupo** vienen **desactivados** (`"_supervisar": false`) para
que no te aparezcan errores de proveedores que no tienes ni procesos de más.

| Se arranca solo | Desactivado (a demanda) |
|---|---|
| ✅ `joker` (tu asistente) | ⏸️ `jokerv1`, `jokerv2` (agentes de ejemplo) |
| ✅ `consola` (la web) | ⏸️ `horas_extras` (ejemplo) |
| ✅ `stt_residente` (voz rápida) | ⏸️ `coordinador` (solo para grupos) |

Para activar cualquiera de ellos: en `configAgentes.json`, pon `"_supervisar": true`
en su entrada y reinicia. La guía para el grupo (dos agentes + coordinador) está en
**[`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)**.

> ⚠️ Cada agente de Telegram necesita **su propio token**. Dos procesos con el mismo
> token se pisan (`409 Conflict`) y uno de los dos deja de responder.

---

## 🧹 Si algo falla


| Síntoma | Causa habitual | Solución |
|---|---|---|
| `No hay TW_TELEGRAM_BOT_TOKEN` | Falta el token en `.env` | Rellena `agent/.env` |
| **`HTTP 401 ... api key is invalid`** | Rellenaste `agent/.env` pero el LLM no coge tus claves | Asegúrate de editar **`agent/.env`** (no solo el de la raíz). Desde v1.0 `configAgentes.json` lee `$env.TW_LLM_API_KEY` |
| El bot no contesta | Otro proceso usa el **mismo token** | Cada bot necesita su token propio (Telegram solo admite un *poller*) |
| `409 Conflict` | Igual que arriba | Cierra el proceso duplicado |
| **`Address already in use`** al reiniciar | El proceso anterior sigue vivo | `python3 -c "import os,signal,glob; [os.kill(int(p.split('/')[-1]), 9) for p in glob.glob('/proc/[0-9]*') if 'run.py' in open(p+'/cmdline','rb').read().decode('utf8','replace')]"` |
| La consola no arranca | Falta Flask/requests | `pip install -r consola/requirements.txt` |
| Voz lenta | El modelo STT se recarga cada vez | Comprueba que el servicio residente está vivo |
| Respuestas raras en voz | Faltan los modelos de `piper` | Revisa la ruta de los modelos en `.env` |
| `⚠️ embeddings reales: False` | No instalaste `sentence-transformers` | Es normal: usa un *fallback*. Si lo quieres semántico: `pip install sentence-transformers` |

> 💡 **Comprueba tu clave antes de nada** (descarta que el problema sea el proveedor):
> ```bash
> curl -s https://api.deepseek.com/chat/completions \
>   -H "Authorization: Bearer TU_CLAVE" -H "Content-Type: application/json" \
>   -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hola"}],"max_tokens":5}'
> ```


---

## 📚 Siguiente lectura

- **[`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)** — monta dos agentes en un grupo
  de Telegram y prueba el trabajo en equipo + la crítica de BigBoss.
- **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — cómo funciona por dentro.

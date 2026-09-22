# NotherClass 🤖🧠

**Un asistente de IA con manos y memoria infinita. Autoalojado. Tuyo.**

[![Licencia: MIT](https://img.shields.io/badge/licencia-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776ab.svg)](https://www.python.org/)
[![Docker listo](https://img.shields.io/badge/docker-listo-2496ed.svg)](INSTALAR_DOCKER.md)
[![Voz local: 0 tokens](https://img.shields.io/badge/voz%20local-0%20tokens-4caf50.svg)](#-voz-local-0-tokens)
[![Sin telemetría](https://img.shields.io/badge/telemetr%C3%ADa-cero-black.svg)](#-lo-que-no-tiene)

NotherClass es una plataforma de agentes autónomos donde **Joker** (tu asistente)
piensa, decide y **ejecuta**: lee y escribe archivos, busca en el código, lanza
comandos, escucha y responde por voz, y **recuerda todo** entre sesiones. Hablas con
él por **Telegram** o desde su **Superconsola web**.

Sin nube de terceros para el motor de voz, sin base de datos gestionada, sin límites
artificiales. Todo corre en **tu** máquina: tu código, tus claves, tu memoria.

---

## 🎬 Una conversación real, sin retoques

Esto **no es un mockup**. Es una prueba completa: instalación desde cero en un
contenedor limpio, bot nuevo de Telegram, clave nueva de LLM. El agente **creó un
agente entero por su cuenta** a partir de una petición en lenguaje natural.

> **🧑 Tú:**
> `podrías crear un agente ?`
>
> **🃏 NotherClass:**
> He mirado el workspace: los agentes viven en `Ag.<Nombre>/` (con `persona.md`) y
> se registran en `configAgentes.json`. Todo listo para crear el tuyo, pero me faltan
> tus datos (no puedo inventarlos).
>
> Dime, en corto:
> 1. **Nombre** del agente.
> 2. **Qué debe hacer** (su rol exacto).
> 3. **Cómo habla** (tono/idioma).
> 4. **Reglas o valores** que debe seguir.
> 5. **Token de Telegram**: créalo con @BotFather (`/newbot`) y pásamelo.
>    Debe ser único (si se repite → error 409).
> 6. **Datos concretos** que deba saber (tareas, horarios, contexto).
>
> Con eso redacto su personalidad y lo creo.
>
> *Hice: revisé la estructura de agentes del proyecto.*
> 🔧 herramientas: 📂 `list_dir` · 🔭 `grep` · 🧠 memoria 2341/20000t (12%)

> **🧑 Tú:**
> `pues quiero un agente para Herramientas como ghbounty o open-bounty-radar hacen
> múltiples búsquedas y consultas por cada escaneo, por lo que sin token agotarías
> el límite en pocos minutos asi que hazlo`
>
> **🃏 NotherClass:**
> Creado y verificado: agente **Bounty** (id=`bounty`) en `Ag.Bounty/`
> (`persona.md`, 58 líneas) + `TOKENS_Y_LIMITES.md` con las claves y los límites de
> cada API, y registrado en `configAgentes.json → agentes.bounty`, con
> **`_supervisar: false` (nace apagado)**.
>
> Sus reglas clave son las que pediste → **sin token no se escanea**, respeta rate
> limits/`Retry-After` sin reintentos en bucle, tokens siempre en `.env` y
> enmascarados, solo targets con scope autorizado y verifica con evidencia antes de
> decir "listo".
>
> Ojo, dato real: en este servidor **no hay ninguna herramienta instalada** todavía
> (ghbounty, subfinder, httpx, nuclei, nmap: todas ausentes), así que aún no puede
> ejecutar nada de verdad.
>
> *Hice: creado el agente Bounty con persona, reglas de tokens/rate limit y doc de límites.*
> 🔧 herramientas: 📂 `list_dir` · 📖 `read_file` · 🔭 `grep` · 🤖 **`crear_agente`** · 🖥️ `run_shell` · ✍️ `write_file` · 🧩 `set_belief`

### 💰 Lo que costó de verdad

| Turno | Entrada | Salida | Caché | **Coste** |
|---|---|---|---|---|
| Entrevista | 20.168 tok | 935 tok | 18.432 | **$0,00113** |
| Creación completa | 106.861 tok | 4.907 tok | 97.152 | **$0,00605** |
| **Total de las 2 interacciones** | | | | **≈ $0,007** |

Lee **106.861 tokens de contexto** (código, estructura, config…) por **medio céntimo**.
Y fíjate en la columna **caché**: de esos 106k, **97k venían del caché del proveedor**,
que cuesta ~30× menos. Eso es la memoria curada funcionando, no teoría.

### 🔍 Qué acababas de ver

- **No inventó nada.** Le faltaban datos → **te los pidió** en vez de suponer.
- **Usó herramientas de verdad**: `crear_agente`, `write_file`, `run_shell`…
- **Verificó el entorno** antes de prometer: comprobó qué había instalado y te lo dijo.
- **Se autorreguló**: creó el agente **apagado** para no arrancar en bucle llamando
  al LLM sin token.
- **Dejó rastro**: qué hizo, qué herramientas usó, qué memoria consumió, qué costó.

Y todo eso con **el 12% de su presupuesto de memoria** (2.341 de 20.000 tokens).

---


## ✨ Qué hace

| | |
|---|---|
| 🧠 **Memoria infinita** | Recuerdo híbrido (BM25 + vectores, fusión RRF) con **selección estricta**: inyecta solo lo relevante, no 80 fragmentos por turno |
| 🛠️ **Manos de verdad** | `run_shell`, `read_file`, `write_file`, `edit_file`, `grep`, `mapa`, `recall`, `remember`… el agente **actúa**, no solo conversa |
| 💬 **Telegram** | Texto y **notas de voz**, con mensaje efímero que muestra qué está haciendo y se borra al terminar |
| 🖥️ **Superconsola web** | Chat con estado EN VIVO, historial, memoria navegable, pizarras y despliegue de "⚙️ ejecutado" para **trazar** lo que hizo |
| 🎙️ **Voz local (0 tokens)** | STT **faster-whisper** + TTS **piper**, todo en local. Hablar no cuesta dinero |
| 🔍 **Revisor automático** | **BigBoss** (2º modelo) + **lilJoker** (intérprete): auditan el código antes de aplicarlo |
| 👥 **Trabajo en equipo** | Varios agentes con memoria propia se hablan y **se reparten el trabajo** en un grupo de Telegram |
| 🐳 **Docker o escritorio** | Docker con Compose **o** instalación directa sin contenedores |


---

## 💡 El potencial

### 1. Ahorro real de tokens
La mayoría de los asistentes envían **todo el historial** en cada turno: cuesta más
y cuanto más hablas, peor va. NotherClass hace lo contrario:

- **Memoria curada, no volcada.** De miles de recuerdos se inyectan ~8 relevantes.
- **Prefijo de sistema estable** → el *cache* del proveedor LLM se reutiliza.
- **Voz gratuita.** STT y TTS son locales: transcribir un audio de 30 s **no gasta un
  solo token**, solo el razonamiento de la respuesta.
- **Presupuesto por turno configurable** (`context_budget_tokens`).
- **Telemetría por turno** (tokens entrada/salida/caché y **coste real**), para que
  sepas exactamente en qué se va cada céntimo.

> Traducción práctica: una conversación larga y con muchos audios puede costar
> **una fracción** de lo que costaría con el patrón "mándame el historial completo".

### 2. Potencia del sistema
- **Ejecuta, no describe.** ¿"Mírame el estado del servidor y arréglalo"? Lo hace.
- **Memoria que crece de verdad.** Creencias, lecciones, resúmenes consolidados y
  episodios con vector: aprende de sus aciertos y errores.
- **Trazabilidad total.** Cada acción queda registrada y puedes **desplegarla** y
  revisarla cuando quieras (no se pierde, no se inventa).
- **Escala a miles de agentes.** Un fichero (`configAgentes.json`) define todos los
  agentes; crear uno nuevo es **añadir una entrada** y su carpeta de memoria.
- **Local y controlado.** Tus claves, tus datos, tu infraestructura. Nada sale de casa
  salvo la llamada al LLM que tú configures.
- **Voz conversacional con *barge-in*.** Detección de actividad por volumen: hablas,
  suelta, y responde. Pensado para el móvil.

### 3. Revisión automática antes de ejecutar (BigBoss + lilJoker)
Cuando el agente va a **editar código**, no lo hace a ciegas:
**lilJoker** interpreta tu intención ("revísalo", en cualquier idioma) y
**BigBoss** —un **segundo modelo distinto**— audita la propuesta **en frío**, con
el contenido real de los archivos, y puede pedir más contexto. Si no lo ve claro,
**no se aplica** y te dice por qué. Tú decides.

### 4. Varios agentes trabajando en equipo (modo grupo)
Telegram **no entrega a un bot los mensajes de otro bot**. NotherClass lo resuelve
con una **pizarra compartida** y un **coordinador** que despierta a cada agente con
el contexto de la conversación: **los agentes se hablan entre ellos y se reparten
el trabajo solos**, con un tope de turnos y pidiéndote confirmación antes de
ejecutar nada.

> 👉 Guía completa, con **cómo montar tu grupo de Telegram paso a paso**:
> [`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)


---

## 🚫 Lo que NO tiene

Tan importante como lo que hace. Esto es lo que **no** vas a encontrar aquí:

| ❌ | Por qué |
|---|---|
| **Nube de terceros para la voz** | STT y TTS corren en tu máquina. Transcribir 30 s **no gasta tokens** |
| **Base de datos gestionada** | Tu memoria es un SQLite en tu disco. Copias el fichero y tienes tu backup |
| **Telemetría, analítica, "phone home"** | Cero. Nada sale de tu casa salvo la llamada al LLM que tú configures |
| **Suscripción, plan, límite artificial** | Es software. Lo instalas y es tuyo |
| **Marca de agua o "powered by"** | Tu asistente, tu nombre, tu branding |
| **Enviar el historial completo cada turno** | Memoria **curada**: ~8 recuerdos relevantes, no 80 fragmentos |
| **Obligación de usar un proveedor concreto** | Cualquier API compatible con OpenAI (DeepSeek, OpenAI, OpenRouter, Z.AI…) |
| **Dependencia de Python 3.11+ o Node** | El núcleo va con **Python 3.8+** y la web **sin frameworks de front** |

> 💡 **Lo único que sale de tu máquina**: la petición al LLM que tú elijas. Nada más.
> Audítalo tú mismo: `grep -rn "requests\|urllib" agent/ --include="*.py"`.

---

## 🎙️ Voz local: 0 tokens

Hablar con él **no cuesta dinero**. El motor de voz es **tuyo**:

| | Motor | Dónde corre | Coste |
|---|---|---|---|
| **Escuchar (STT)** | faster-whisper / whisper.cpp | Tu CPU | **$0** |
| **Hablar (TTS)** | piper (voces neuronales) | Tu CPU | **$0** |

- **Voz conversacional con *barge-in*.** Detección de actividad por volumen: hablas,
  sueltas, y responde. Pensado para usar andando por la calle.
- **Sin markdown leído en alto.** Convierte `**negrita**`, emojis, URLs, tablas y
  símbolos en texto que **suena natural** (*"$5"* → *"cinco dólares"*).
- **Voces en español e inglés** (el traductor responde con voz inglesa, no con acento).
- **Se descarga sola** la primera vez (~60 MB) y se queda en caché.
- **Funciona sin conexión** al proveedor de voz: si te quedas sin internet, el TTS
  te avisa y el agente sigue en texto.

---

## 🚀 Instalación

Elige **un** camino. Los dos dejan el mismo sistema funcionando.

| Camino | Guía | Para quién |
|---|---|---|
| 🐳 **Docker** | [`INSTALAR_DOCKER.md`](INSTALAR_DOCKER.md) | Lo más cómodo: todo incluido |
| 🖥️ **Escritorio** | [`INSTALAR_ESCRITORIO.md`](INSTALAR_ESCRITORIO.md) | Sin contenedores, control total |

<details>
<summary><b>Vista rápida — Docker</b></summary>

```bash
git clone https://github.com/<tu-usuario>/notherclass.git
cd notherclass

cp .env.compose.example .env          # TZ + puerto
cp agent/.env.example agent/.env      # tu clave del LLM y el token de Telegram

docker compose -p nc -f docker-compose.yml -f docker-compose.dev.yml up -d --build
curl -s http://localhost:5000/health  # {"ok":true,"servicio":"consola-agentes"}
```
</details>

<details>
<summary><b>Vista rápida — Escritorio</b></summary>

```bash
git clone https://github.com/<tu-usuario>/notherclass.git
cd notherclass

cp agent/.env.example agent/.env
pip install -r agent/requirements.txt -r consola/requirements.txt

cd agent   && python3 run.py --oneshot "hola, preséntate"   # prueba sin Telegram
cd ../consola && python3 -u app.py                          # http://localhost:5000
```
</details>

> 📖 **¿Primera vez?** Sigue [`COMO_PROBARLO.md`](COMO_PROBARLO.md): qué
> necesitas antes, cómo saber si funcionó y qué hacer si algo falla.


---

## ⚙️ Configuración mínima

`agent/.env`:

```ini
TW_AGENT_NAME=Joker
TW_AGENT_STORE=Forja                    # da nombre a su base de memoria
TW_AGENT_PERSONA=AGENTS/notherclass-joker.md

TW_LLM_PROVIDER=deepseek                # cualquier API compatible con OpenAI
TW_LLM_API_KEY=tu_clave_aqui
TW_LLM_MODEL=deepseek-chat               # o deepseek-reasoner (con razonamiento)

TW_TELEGRAM_BOT_TOKEN=                  # vacío = solo consola web

FW_SIZE=base                            # modelo STT: base | tiny
FW_THREADS=2                            # súbelo si tu CPU tiene más núcleos
```

> 📄 Todas las variables explicadas: [`agent/.env.example`](agent/.env.example)

---

## 🧠 Cómo funciona (arquitectura en 30 segundos)

```
   Tú ──► Telegram ──┐
                      ├──► run.py  (el agente: persona + memoria + herramientas)
   Tú ──► Consola ───┘         │
   web   (5000)                ├─► brain/     memoria híbrida (SQLite + vectores)
                               ├─► core/llm   el modelo (API compatible OpenAI)
                               ├─► core/audio STT whisper + TTS piper (LOCAL)
                               └─► herramientas: shell, ficheros, grep, recall…

   supervisor ──► mantiene vivos agentes y servicios (auto-arranque, anti-zombis)
   consola/   ──► web: chat en vivo, memoria, pizarras, métricas
```

1. Llega un mensaje (texto o voz) desde Telegram o desde la web.
2. **Contexto curado**: persona + creencias + recuerdos relevantes + conversación
   reciente. No el historial entero.
3. El modelo decide y **usa herramientas** si hace falta.
4. Se guarda la experiencia (episodio + lo aprendido).
5. Responde en texto y, si toca, **en voz**.

📄 Más detalle: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)

---

## 🤖 Crear tu propio agente

Tienes **dos formas**:

### A) Pídeselo a Joker (conversando)

Escríbele algo como:

```
Quiero crear un agente nuevo
```

**Joker te entrevistará antes de crear nada** (no lo hace a la primera). Te
preguntará, en lenguaje natural:

1. **¿Cómo se llamará?**
2. **¿Qué debe hacer exactamente?** (su propósito)
3. **¿Cómo quieres que hable?** (tono, estilo, idioma)
4. **¿Qué valores o reglas debe seguir?**
5. **¿Tiene token de Telegram?** — si no, te dirá cómo crearlo con **@BotFather**
6. **¿Datos concretos que deba conocer?** (tareas, horarios, contexto…)

Cuando tenga **todo**, redacta su personalidad completa y crea el agente. Después
te resume: dónde editar su persona, dónde vive su memoria y **que queda
desactivado** hasta que tú lo actives.

> 💡 El agente nuevo **nace apagado** (`"_supervisar": false`) a propósito: así no
> arranca en bucle intentando llamar al LLM sin token. Lo activas cuando esté listo.

### B) A mano (copiando la plantilla)

1. Copia la plantilla de personalidad:
   ```bash
   cp agent/AGENTS/_plantilla_agente.md agent/AGENTS/mi-agente.md
   # edita {NOMBRE}, {ROL} y {VALORES}
   ```
2. Añade su entrada en `configAgentes.json`:
   ```json
   "mi-agente": {
     "name": "Mi Agente",
     "store": "mi_agente",
     "persona": "AGENTS/mi-agente.md",
     "telegram_bot_token": "$env.TW_MI_AGENTE_TOKEN"
   }
   ```
3. Listo: `python run.py --agent mi-agente`

Su memoria es **suya** (`MEMORY/brain_mi_agente.sqlite3`) y no se mezcla con la de los
demás. Para varios agentes cooperando hay pizarra compartida y coordinador.

> Los agentes de ejemplo (`jokerv1`, `jokerv2`, `horas_extras`) son **solo eso**:
> ejemplos. Bórralos y crea los tuyos.

---

## 🗺️ Estructura del proyecto

```
agent/                  el agente
  run.py                bucle del agente + API local de turnos
  core/                 llm, audio (STT/TTS), supervisor, config
  brain/                memoria: store, embeddings, consolidación, lecciones
  channels/telegram.py  puente con Telegram
  AGENTS/               personalidades (una por agente)
consola/                Superconsola web (Flask + front sin dependencias)
translator/             bot traductor de ejemplo
Dockerfile · docker-compose*.yml
```

---

## 🔒 Seguridad

- La consola **puede ejecutar comandos**: nunca la expongas sin autenticación. Detrás
  de nginx, usa **Basic Auth** (o un túnel/VPN).
- `.env`, `MEMORY/`, `*.sqlite3` y `grupo_bus/` están en `.gitignore` **por diseño**.
- En producción: HTTPS, `fail2ban` y cerrar todo lo que no uses.

---

## 🎯 Pruébalo en 3 comandos

```bash
git clone https://github.com/<tu-usuario>/notherclass.git && cd notherclass
cp agent/.env.example agent/.env      # pon tu clave del LLM (2 minutos)
cd agent && python3 run.py --oneshot "hola, preséntate"
```

Sin Telegram, sin Docker, sin configurar nada más. Si ves su respuesta, **ya funciona**.
Después conectas Telegram y tienes el asistente completo.

> 📖 **¿Primera vez?** Sigue [`COMO_PROBARLO.md`](COMO_PROBARLO.md): qué necesitas
> antes, cómo saber si funcionó y **qué hacer si algo falla** (con los errores reales
> que nos encontramos y su causa).

### ¿Y luego qué?

- 🎙️ **Mándale una nota de voz** por Telegram → verás la transcripción y te contesta hablando.
- 🤖 **Dile** *"quiero crear un agente nuevo"* → te entrevista y lo crea solo.
- 👥 **Monta un grupo** de varios agentes → [`GRUPO_Y_REVISION.md`](GRUPO_Y_REVISION.md)
- 🖥️ **Abre la Superconsola** (`http://localhost:5000`) → mira su memoria, su estado en
  vivo y **qué hizo exactamente** en cada turno.

---

## 🛠️ ¿Se te atasca algo?

Abre un **issue** con tu sistema, versión de Python y el log: con eso se arregla rápido.

Si te sirve, una ⭐ ayuda a que otros lo encuentren. Y si construyes algo con esto,
cuéntamelo — me interesa mucho más el *qué* que el *cuántos*.

---

## 📜 Licencia

**MIT** © 2026 **Erick Gabriel Palma Montufar** — `dabaixxx@gmail.com`

Úsalo, modifícalo, véndelo si quieres: solo mantén el aviso de copyright.
Consultas, colaboraciones o soporte: **dabaixxx@gmail.com**


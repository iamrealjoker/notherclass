# Trabajo en grupo y revisión automática

Dos capacidades del asistente que van más allá de "un bot que responde":
**varios agentes que colaboran en un grupo de Telegram** y un **segundo modelo
que audita el código antes de aplicarlo**.

---

## 1. BigBoss + lilJoker: la crítica antes de ejecutar

El problema de un agente que **edita código** es que puede equivocarse y
destrozarte el proyecto sin que te enteres. NotherClass lo resuelve con **dos
personajes** que trabajan en equipo:

```
   Tú pides un cambio
          │
          ▼
   ┌─────────────────┐
   │     JOKER       │  propone la edición (NO la aplica todavía)
   │  (tu asistente) │
   └─────────────────┘
          │ propuesta
          ▼
   ┌─────────────────┐   interpreta TU INTENCIÓN (en cualquier idioma):
   │    lilJoker     │   ¿estás pidiendo una revisión?
   │  (intérprete)   │   "revisalo", "que lo valide", "échale un ojo"…
   └─────────────────┘
          │
          ▼
   ┌─────────────────┐   audita la propuesta EN FRÍO, con el contenido real
   │    BIGBOSS      │   de los archivos. Puede PEDIR contexto bajo demanda.
   │  (crítico 2º)   │
   └─────────────────┘
          │
    ┌─────┴──────┐
    ▼            ▼
  ✅ OK        ⚠️ REVISAR
  se aplica    NO se aplica + te dice por
  el cambio    qué y qué propondría
```

### Por qué tiene valor
- **Los dos modelos son distintos** (`TW_REVIEW_MODEL` ≠ modelo principal): el
  revisor no arrastra el sesgo del que escribió el código.
- **Auditoría en frío**: BigBoss lee el **contenido real** de los archivos, no
  la descripción que le da Joker.
- **Puede pedir contexto**: si necesita ver otro fichero para opinar, lo pide
  (`TW_REVIEW_MAX_FETCHES`).
- **Se activa solo**: no tienes que invocar nada; lilJoker decide por intención.
- **Es reversible**: la propuesta queda en pausa y **tú decides** ("sí" / "no" /
  "cancelar").

### Cómo se activa
```ini
TW_ENABLE_REVIEW=1              # revisor activado
TW_REVIEW_MODEL=...             # modelo crítico (distinto del principal)
TW_REVIEW_API_KEY=...
TW_CONFIRM_EDITS=1              # el agente PROPONE y espera tu OK antes de escribir
```
En el chat, cuando el agente va a editar código, te pide permiso. Puedes decir
*"dale"* o *"que lo revise BigBoss antes"* — y lilJoker lo entiende.

---

## 2. Modo grupo: varios agentes colaborando (coordinador)

Telegram tiene una limitación: **un bot NO recibe los mensajes que escribe otro
bot**. Si JokerV1 responde en el grupo, JokerV2 nunca lo ve. Resultado: la
conversación entre agentes no se sostiene sola.

NotherClass lo resuelve con una **pizarra compartida** + un **coordinador**:

```
   GRUPO DE TELEGRAM (tú + 2 bots)
        │
        │  cada mensaje se ANOTA en la pizarra
        ▼
   grupo_bus/chat_<id>.jsonl      ← la "pizarra" (lo que dijo cada uno)
        ▲
        │  alguien menciona a un bot…
        │
   ┌─────────────────────┐
   │    COORDINADOR      │  despierta a ese bot con el CONTEXTO de la pizarra
   │  (auto-trigger)     │  y su respuesta se anota → despierta al otro
   └─────────────────────┘
```

**Así la charla entre agentes fluye sola**, con un **tope de turnos** para no
gastar sin control.

### El protocolo en 2 fases (seguridad por diseño)

| Fase | Qué pasa | Tope |
|---|---|---|
| **1. Conversación** | Los bots hablan por la pizarra, acuerdan un plan y te **PIDEN CONFIRMACIÓN** | 4 turnos |
| **Confirmación** | **lilJoker** interpreta tu "hazlo" / "ok, adelante" (por intención, en cualquier idioma) | — |
| **2. Ejecución** | Se **reparten** el trabajo ("yo hago X, tú Y"), con un **candado** para no tocar el mismo archivo | 6 turnos |

- `turno_grupo.py` **fuerza `TW_CONFIRM_EDITS=1`**: toda escritura real queda
  **en pausa** hasta tu "sí".
- **Un solo coordinador** a la vez (lock con `flock`).
- `--dry-run` y `--simular` para probar **sin gastar tokens** ni enviar nada.

---

## 3. Cómo probarlo tú mismo, paso a paso

### a) Crea dos bots
En Telegram, habla con **@BotFather**:
1. `/newbot` → nombre (p. ej. `JokerV1`) → usuario (`jokerv1_bot`) → **guarda el token**.
2. Repite para el segundo (`jokerv2_bot`) → **otro token**.

> ⚠️ **Cada bot necesita SU PROPIO token.** Como cada bot hace *long polling*,
> dos procesos distintos con el mismo token se **pisan** (error 409).

### b) Crea un grupo y añade los dos bots
1. Crea un **grupo** en Telegram (p. ej. *"NotherClass Lab"*).
2. Añade a los **dos bots** como miembros.
3. **Desactiva el modo privacidad** de ambos (**@BotFather → `/setprivacy` → Disable**),
   para que puedan leer lo que se escribe en el grupo.
4. Añádete tú también.

### c) Activa los dos agentes (vienen desactivados)

Los agentes de ejemplo están **apagados por defecto**. Para el grupo necesitas
**dos**. En `configAgentes.json`, en las entradas `jokerv1` y `jokerv2`:

```json
"jokerv1": {
  "name": "JokerV1",
  "_supervisar": true,          ← quita esta línea o ponla en true
  "store": "jokerv1",
  "carpeta": "Ag.JokerV1",
  "telegram_bot_token": "$env.TW_TOKEN_V1"
}
```

Haz lo mismo con `jokerv2`. Y en `servicios_extra`, activa el coordinador:

```json
"coordinador": {
  "_supervisar": true,          ← activa el coordinador
  "cmd": "python3 -u coordinador.py",
  "cwd": "agent"
}
```

### d) Pon los tokens en el `.env`

```ini
TW_TOKEN_V1=token_del_bot_jokerv1
TW_TOKEN_V2=token_del_bot_jokerv2
```

### e) Configura los agentes
Cada bot es un agente con su **propio** token y su **propia** memoria:

```json
"jokerv1": {
  "name": "JokerV1",
  "store": "jokerv1",
  "carpeta": "Ag.JokerV1",
  "persona": "{carpeta}/persona.md",
  "memory_dir": "{carpeta}/memory",
  "http_port": 5053,
  "telegram_bot_token": "$env.TW_TOKEN_V1"
},
"jokerv2": {
  "name": "JokerV2",
  "store": "jokerv2",
  "carpeta": "Ag.JokerV2",
  "http_port": 5054,
  "telegram_bot_token": "$env.TW_TOKEN_V2"
}
```

### f) Arráncalos
```bash
python run.py --agent jokerv1        # o deja que el supervisor los levante
python run.py --agent jokerv2
python coordinador.py                # el coordinador que da vida al grupo
```

### g) Prueba el potencial
En el grupo, **menciona a uno** y deja que se coordinen:

```
@jokerv1_bot revisa el proyecto con @jokerv2_bot y proponedme un plan
```

Verás cómo:
1. Se hablan entre ellos por la pizarra (aunque Telegram no se lo entregue).
2. Acuerdan un plan y **te piden permiso**.
3. Tú dices *"adelante"* → se reparten el trabajo **con la crítica de BigBoss**
   antes de cada escritura.

### h) Probar sin gastar nada
```bash
python coordinador.py --dry-run       # muestra lo que haría, sin ejecutar
python coordinador.py --simular "@jokerv2 hola, explica a jokerv1 la idea"
```

---

## 4. Por qué esto es un potencial real

- **No es un bot suelto, es un equipo.** Varios agentes con **memorias
  independientes** que se reparten trabajo y se leen entre ellos.
- **Auto-organizado**: nadie tiene que ir mencionando a cada bot turno a turno;
  el coordinador mantiene el hilo.
- **Con red de seguridad**: plan → tu confirmación → reparto → crítica
  automática → ejecución. Cuatro barreras antes de tocar tu código.
- **Ahorra**: los topes de turnos y el `--dry-run` evitan gastar de más, y todo
  queda registrado en la pizarra para que puedas **auditarlo después**.


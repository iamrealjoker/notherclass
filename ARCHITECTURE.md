# NotherClass — Arquitectura

> Cómo está construido el asistente: memoria, contexto, herramientas y voz.
> Este documento describe lo que **existe y funciona hoy**, no un plan futuro.

---

## 1. Modelo mental: un agente es un asistente con memoria

Cada agente se modela con dos piezas:

```
          ┌──────────────────────────────────────────────┐
          │           IDENTIDAD (persona)                │   ← quién es
          │   nombre · rol · valores · estilo de habla   │   (persona.md)
          └──────────────────────────────────────────────┘
                          │ ancla
          ┌──────────────────────────────────────────────┐
          │            MEMORIA (cerebro)                 │   ← qué sabe
          │   episódica · creencias · lecciones ·        │
          │   resúmenes   (BM25 + vectores + RRF)        │
          └──────────────────────────────────────────────┘
```

- La **identidad** casi no cambia: es el contrato de personalidad del agente.
- La **memoria** cambia a cada instante y es **infinita**.
- El **turno** es el proceso que une ambas: el agente consulta su memoria
  (recuerda), actúa con sus herramientas, y guarda lo vivido (aprende).


---

## 2. Los 4 tipos de memoria

| Tipo | Implementación | Función |
|------|----------------|---------|
| Episódica | `chunks` (texto + vector + ts) | "qué viví" |
| Declarativa | `beliefs` (clave→valor, confidence, fuente) | "qué creo" |
| Aprendizaje | `lessons` (victorias y fallos) | "qué he aprendido" |
| De trabajo | `working` (ventana reciente en contexto) | "qué tengo en la cabeza ahora" |

La **memoria infinita** no significa volcarlo todo en el prompt: significa que
**nada importante se pierde**, porque el pipeline de consolidación lo destila a
creencias + resúmenes + lecciones, y el recall trae lo relevante **con
presupuesto de tokens**.

---

## 3. El turno: de un mensaje a una respuesta

```
 mensaje del humano (texto o voz)
        │
        ▼
 ┌───────────────────┐  guardar   ┌───────────────────────┐
 │   EVENTO          │ ─────────► │  chunk episódico      │
 │ (lo que dije)     │            │  texto + vector       │
 └───────────────────┘            └───────────────────────┘
        │
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  RECALL: BM25 + vectores → fusión RRF → 8 mejores +          │
 │  recientes  =  CONTEXTO CURADO (persona + creencias +        │
 │  recuerdos relevantes + conversación reciente)               │
 └──────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌───────────────────┐   si hace falta   ┌───────────────────┐
 │  LLM decide       │ ────────────────► │  HERRAMIENTAS     │
 │  (tool calling)   │ ◄──────────────── │ shell/ficheros/…  │
 └───────────────────┘    resultado      └───────────────────┘
        │
        ▼
  respuesta al humano (texto y/o voz) + se guarda lo aprendido
```

---

## 4. Esquema real de la memoria (SQLite)

> Un solo fichero por agente: `MEMORY/brain_<store>.sqlite3`.
> Sin servidor de base de datos: cero dependencias, cero fricción.

**Tablas**

- `chunks` — memoria episódica: `id, agent, kind, text, ts, tokens, meta, vector`.
  Es lo que el agente ha vivido (lo que dijiste tú y lo que respondió él).
- `beliefs` — creencias: `key, value, confidence, source, last_accessed`.
  Lo que el agente **da por cierto** sobre el mundo y sobre ti.
- `lessons` — aprendizaje: `kind (win|fail), title, body, outcome`.
  Aciertos y errores, para no repetir los unos y repetir los otros.
- `summaries` — resúmenes consolidados de tramos antiguos (compresión).
- `meta` — estado interno (marcas de procesos, versión, etc.).

**Búsqueda:** cada chunk guarda su **vector** de embedding. El recall combina
**BM25** (palabras clave) + **similitud vectorial** y fusiona ambos rankings con
**RRF** (Reciprocal Rank Fusion).

> Si `sentence-transformers` no está instalado, hay un **fallback** de embeddings
> por *hash*: funciona igual, con algo menos de precisión semántica. Nunca falla.

---

## 5. Recordar: `recall(query, presupuesto)`

1. **BM25** sobre el texto de los chunks del agente.
2. **Vectores** sobre los mismos chunks + creencias.
3. **Fusión RRF** de los dos rankings.
4. **Selección estricta**: se queda con los ~8 mejores (`recall_max_items`) y
   descarta los que puntúan por debajo de un umbral (`recall_min_ratio`).
5. **Corte por presupuesto de tokens** (`context_budget_tokens`). La *working
   memory* (lo más reciente) **siempre entra** y tiene prioridad.

Resultado: el agente puede tener **años de historial** y responder recordando
solo lo pertinente. **Memoria infinita, contexto finito.**

---

## 6. Consolidación: cómo la memoria "no se acaba"

Dos mecanismos para que la memoria crezca sin saturar el contexto:

- **Compresión**: tramos antiguos de conversación se agrupan en `summaries`
  (se libera el detalle bruto, se conserva lo esencial).
- **Destilación**: de lo vivido se actualizan `beliefs` con `confidence`
  (sube si se repite, baja si se contradice) y `lessons` (victorias y fallos).

La consolidación **no corre en bucle** por defecto: se dispara cuando tú quieres
(comando `/consolidar` en Telegram, o `run.py --consolidate`), para no gastar
tokens sin que lo pidas.



---

## 7. Herramientas: las "manos" del agente

El agente no solo conversa: **actúa** mediante *tool calling* nativo del modelo.

| Herramienta | Qué hace |
|---|---|
| `run_shell` | Ejecuta comandos en el entorno del agente |
| `read_file` / `write_file` / `edit_file` | Lee, crea y modifica archivos del workspace |
| `grep` / `mapa` | Busca en el código y genera un mapa del proyecto |
| `recall` / `remember` / `set_belief` | Consulta y escribe en su memoria |
| `list_dir` | Explora carpetas |

**Reglas de seguridad del diseño:**

- El agente trabaja sobre un **workspace** (`TW_WORKSPACE`). Las herramientas de
  archivos resuelven rutas **relativas a ese workspace** y rechazan lo que se sale.
- Las escrituras pueden requerir **confirmación humana** (`TW_CONFIRM_EDITS=1`):
  el agente propone y espera tu "sí" antes de tocar nada.
- Un **revisor automático** (segundo modelo) audita el código cuando el agente
  edita, antes de darlo por bueno (`TW_ENABLE_REVIEW`).

---

## 8. Voz local (STT + TTS)

Todo ocurre en tu máquina: **hablar no gasta tokens**.

```
 audio del móvil/PC ──► ffmpeg ──► faster-whisper (modelo precargado) ──► texto
                                                                          │
 texto de respuesta ──► piper (voz local) ──► audio ──► se reproduce      │
```

- **STT**: `faster-whisper` como **proceso residente** (carga el modelo una sola
  vez y atiende por HTTP local). Con un modelo sin precargar, cada nota tardaría
  ~4-5 s más.
- **TTS**: `piper` con voces locales (español e inglés).
- El texto se **humaniza** antes de leerlo: fuera asteriscos, emojis, URLs,
  comillas y paréntesis (para que no suene a robot).

---

## 9. Multi-agente

- `configAgentes.json` define **todos** los agentes: identidad, memoria y claves.
- Cada agente = **un proceso** (`run.py --agent <id>`) con **su propia** BD de
  memoria. **Cero mezcla** entre agentes.
- El **supervisor** interno mantiene vivos agentes y servicios, y los relanza si
  caen.
- Varios agentes pueden compartir una **pizarra** para coordinarse.

---

## 10. Principios de diseño

- **Nada de dependencias pesadas para lo esencial**: memoria en SQLite, voz local,
  stdlib en su mayoría.
- **Graceful fallback siempre**: si falta un embedding real, si el servicio de voz
  no está, si el revisor falla… el sistema sigue funcionando, degradado pero vivo.
- **Contexto acotado por tokens**: nunca se manda la historia entera al modelo.
- **Aislamiento por agente**: toda la memoria va indexada por `agent_id`.
- **Todo verificable**: cada acción se registra y se puede **trazar** después.


---

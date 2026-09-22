# Cómo crear un bot de Telegram propio
> Guía práctica: cómo montar un bot independiente reutilizando el núcleo del agente.

---

## 1. Estructura mínima de un bot

Un bot vive en su propia carpeta. Cada bot es un proceso Python
independiente que habla con Telegram usando el mismo núcleo del agente.



---

## 7. Fallo común: dos procesos para el mismo bot

Telegram **solo permite una conexión de polling activa por bot**. Si lanzas
dos procesos con el mismo token, el segundo recibe HTTP 409 y se queda
callado (consume el mensaje pero no responde).

**Síntoma:** escribes `hola`, no responde, pero el proceso está vivo.

**Solución:** antes de relanzar, mata los procesos anteriores:


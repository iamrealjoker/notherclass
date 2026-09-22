// modo_humano.js — capa "Modo humano" de la Superconsola NotherClass (JokerV2)
// ---------------------------------------------------------------------------
// Toggle OFF -> app.js pinta el JSON crudo (como hasta ahora).
// Toggle ON  -> esta capa pide ?humano=1 a la API del 5000 y pinta:
//   · tablas legibles por tipo (creencias, recuerdos, lecciones, persona…)
//   · embeddings OCULTOS: solo un contador "vector de N dims"
//   · mensajes estructurados: quién (Tú/Agente) · qué · cuándo (fecha)
//   · edición INLINE solo de campos seguros (nunca vectores), vía
//     POST /api/memoria/<aid>/editar, que el backend valida igualmente.
//
// Contrato: window.ModoHumano.render(contenedorEl, endpoint, modoHumano)
//   - si modoHumano=false, NO hace nada (deja el crudo a app.js).
// Nunca toca /api/menu ni el arranque; solo lee/formatea los endpoints dados.
(function () {
  'use strict';

  const POR_PAG = 25;
  // columnas "feas" que en modo humano se resumen u ocultan
  const OCULTAS = { embedding: 1 };

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fechaDe(ts) {
    if (ts == null || ts === '') return '';
    const n = Number(ts);
    if (!isFinite(n)) return String(ts);
    try {
      return new Date(n * 1000).toLocaleString('es-ES', { hour12: false });
    } catch (e) { return String(ts); }
  }
  function dimsDe(v) {
    // v puede ser "0.0, 0.0, ..." o "[0.0, ...]" o array
    try {
      let arr = v;
      if (typeof v === 'string') arr = JSON.parse(v);
      if (Array.isArray(arr)) return arr.length;
    } catch (e) { /* noop */ }
    if (typeof v === 'string') {
      // "[0.0, 0.0, 0.0]" contado a mano
      const trozos = v.replace(/^\[|\]$/g, '').split(',').map(s => s.trim()).filter(s => s);
      if (trozos.length && trozos.every(s => !isNaN(Number(s)))) return trozos.length;
    }
    if (Array.isArray(v)) return v.length;
    return null;
  }
  function tipoDe(endpoint) {
    const m = /[?&]tipo=([a-z_]+)/i.exec(endpoint || '');
    return m ? m[1].toLowerCase() : '';
  }
  async function api(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(r.status + ' ' + url);
    return r.json();
  }
  function conHumano(url) {
    return url + (url.indexOf('?') === -1 ? '?' : '&') + 'humano=1';
  }

  // ---------- estilos mínimos (autocontenidos, no dependen del CSS global) ----------
  function estilos() {
    if (document.getElementById('mh-estilos')) return;
    const css = document.createElement('style');
    css.id = 'mh-estilos';
    css.textContent =
      // contenedor con scroll horizontal CONTROLADO en móvil (no rompe el layout)
      '.mh-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;padding-bottom:2px}' +
      // neutraliza el table{display:block} global del CSS móvil: la tabla sigue siendo tabla
      '.mh-scroll table{display:table;width:auto;min-width:100%}' +
      '.mh-tabla{border-collapse:collapse;font-size:13px;width:100%}' +
      '.mh-tabla th{text-align:left;padding:6px 10px;border-bottom:1px solid #30363d;color:#58a6ff;white-space:nowrap}' +
      '.mh-tabla td{padding:6px 10px;border-bottom:1px solid #21262d;vertical-align:top}' +
      '.mh-tabla tr:hover td{background:#161b22}' +
      '.mh-meta{display:inline-block;background:#21262d;color:#8b949e;border-radius:6px;padding:1px 6px;font-size:11px;margin:1px 2px}' +
      '.mh-vec{color:#8b949e;font-style:italic}' +
      '.mh-edit{cursor:pointer;border-bottom:1px dashed #58a6ff}' +
      '.mh-edit:hover{background:#1f6feb22}' +
      '.mh-input{background:#0d1117;color:#e6edf3;border:1px solid #58a6ff;border-radius:6px;padding:4px 6px;width:100%;font:inherit}' +
      '.mh-msg{border:1px solid #21262d;border-radius:10px;padding:8px 10px;margin:6px 0;background:#0d1117}' +
      '.mh-msg .quien{font-weight:600;color:#58a6ff}' +
      '.mh-msg .quien.agente{color:#3fb950}' +
      '.mh-msg .cuando{font-size:11px;color:#8b949e;margin-left:8px}' +
      '.mh-msg .que{margin-top:4px;white-space:pre-wrap;word-break:break-word}' +
      '.mh-pag{display:flex;gap:8px;align-items:center;margin:8px 0;font-size:13px}' +
      '.mh-pag button{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:3px 10px;cursor:pointer}' +
      '.mh-pag button:disabled{opacity:.4;cursor:default}' +
      '.mh-estado{opacity:.7;font-size:12px;margin-left:8px}' +
      // ficha legible de 👤 Persona (modo humano)
      '.mh-persona{max-width:760px;background:#0d1117;border:1px solid #30363d;border-radius:12px;padding:14px 16px;margin:4px 0}' +
      '.mh-p-titulo{font-weight:700;color:#58a6ff;font-size:15px;margin:12px 0 4px}' +
      '.mh-p-titulo.h1{font-size:18px;color:#e6edf3;margin-top:0}' +
      '.mh-p-parrafo{margin:6px 0;line-height:1.5;color:#e6edf3;white-space:pre-wrap;word-break:break-word}' +
      '.mh-p-item{margin:3px 0 3px 14px;line-height:1.45;color:#c9d1d9;word-break:break-word}';
    document.head.appendChild(css);
  }

  // ---------- render de una celda ----------
  function celdaValor(campo, val) {
    if (OCULTAS[campo]) {
      const n = dimsDe(val);
      return '<span class="mh-vec">vector de ' + (n == null ? '?' : n) + ' dims, oculto en modo humano</span>';
    }
    if (campo === 'fecha' || campo === 'ts') {
      const f = /^\d+(\.\d+)?$/.test(String(val)) ? fechaDe(val) : esc(val);
      return esc(f);
    }
    if (val && typeof val === 'object') {
      return Object.keys(val).map(k =>
        '<span class="mh-meta">' + esc(k) + ': ' + esc(String(val[k]).slice(0, 40)) + '</span>').join(' ') ||
        '<span class="mh-vec">(vacío)</span>';
    }
    const s = String(val == null ? '' : val);
    return esc(s.length > 400 ? s.slice(0, 400) + '…' : s);
  }

  // ---------- tabla genérica con edición inline ----------
  function tabla(items, tipo, editable, aid, recargar) {
    if (!items.length) return '<div class="mh-estado">Sin datos.</div>';
    const claves = [];
    items.forEach(it => Object.keys(it).forEach(k => { if (claves.indexOf(k) === -1) claves.push(k); }));
    // pone id/key al principio para legibilidad
    ['id', 'key', 'fecha', 'ts'].forEach(k => {
      const i = claves.indexOf(k); if (i > 0) { claves.splice(i, 1); claves.unshift(k); }
    });
    let h = '<div class="mh-scroll"><table class="mh-tabla"><tr>' +
      claves.map(k => '<th>' + esc(k) + '</th>').join('') + '</tr>';
    items.forEach((it, idx) => {
      const clave = it.key != null ? it.key : (it.id != null ? it.id : it.rowid);
      h += '<tr>' + claves.map(k => {
        let v = it[k];
        const editableAqui = editable && editable.indexOf(k) !== -1 && clave != null;
        if (editableAqui) {
          // guardamos el valor en data-attrs para el handler de edición
          return '<td><span class="mh-edit" data-tipo="' + esc(tipo) + '" data-clave="' + esc(clave) +
            '" data-campo="' + esc(k) + '" data-idx="' + idx + '">' + celdaValor(k, v) + '</span></td>';
        }
        return '<td>' + celdaValor(k, v) + '</td>';
      }).join('') + '</tr>';
    });
    h += '</table></div>';
    if (editable && editable.length)
      h += '<div class="mh-estado">✏️ Editable aquí: ' + editable.map(esc).join(', ') +
        ' · haga clic en el valor para editarlo (vectores nunca se tocan).</div>';
    return h;
  }

  // ---------- persona: ficha legible del persona.md ----------
  function personaBonita(item) {
    const texto = (item && item.texto) || '';
    const fuente = (item && item.fuente) || '';
    // separa por líneas y detecta encabezados markdown (# / ##) y viñetas (-)
    const lineas = texto.split('\n');
    let h = '<div class="mh-persona">';
    lineas.forEach(function (ln) {
      const t = ln.trim();
      if (!t) return;
      if (/^#{1,6}\s/.test(t)) {
        const nivel = Math.min(t.match(/^#+/)[0].length, 3);
        h += '<div class="mh-p-titulo' + (nivel === 1 ? ' h1' : '') + '">' +
          esc(t.replace(/^#{1,6}\s*/, '')) + '</div>';
      } else if (/^[-*•]\s/.test(t)) {
        h += '<div class="mh-p-item">• ' + esc(t.replace(/^[-*•]\s*/, '')) + '</div>';
      } else {
        h += '<div class="mh-p-parrafo">' + esc(t) + '</div>';
      }
    });
    if (!lineas.some(function (l) { return l.trim(); }))
      h += '<div class="mh-estado">Este agente aún no tiene persona definida.</div>';
    h += '</div>' +
      (fuente ? '<div class="mh-estado">📄 fuente: ' + esc(fuente) + '</div>' : '');
    return h;
  }

  // ---------- mensajes estructurados (quién / qué / cuándo) ----------
  function mensajes(msgs) {
    if (!msgs.length) return '<div class="mh-estado">Sin mensajes.</div>';
    return msgs.map(m => {
      const autor = m.autor || m.quien || '';
      const esAgente = /agent|bot|joker|ia|assistant/i.test(String(autor));
      const cuando = fechaDe(m.ts);
      const para = m.para ? ' <span class="mh-meta">para: ' + esc(m.para) + '</span>' : '';
      const que = m.texto || m.contenido || '';
      return '<div class="mh-msg">' +
        '<span class="quien ' + (esAgente ? 'agente' : 'humano') + '">' + esc(autor || '¿?') + '</span>' +
        (cuando ? '<span class="cuando">' + esc(cuando) + '</span>' : '') + para +
        '<div class="que">' + esc(que) + '</div>' +
        '</div>';
    }).join('');
  }

  // ---------- edición inline ----------
  function activarEdicion(cont, aid, recargar) {
    cont.querySelectorAll('.mh-edit').forEach(sp => {
      sp.addEventListener('click', () => {
        if (sp.dataset.editando === '1') return;
        const original = sp.textContent;
        const campo = sp.dataset.campo;
        const tipo = sp.dataset.tipo;
        const clave = sp.dataset.clave;
        const input = document.createElement('input');
        input.className = 'mh-input';
        input.value = original;
        sp.dataset.editando = '1';
        sp.innerHTML = '';
        sp.appendChild(input);
        input.focus();
        let cerrado = false;
        const cancelar = () => {
          if (cerrado) return; cerrado = true;
          sp.dataset.editando = '0';
          sp.textContent = original;
        };
        const guardar = async () => {
          if (cerrado) return; cerrado = true;
          const nuevo = input.value;
          if (nuevo === original) { sp.dataset.editando = '0'; sp.textContent = original; return; }
          sp.dataset.editando = '0';
          sp.textContent = '⏳ guardando…';
          try {
            const campos = {}; campos[campo] = nuevo;
            const r = await api('/api/memoria/' + encodeURIComponent(aid) + '/editar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ tipo: tipo, clave: clave, campos: campos })
            });
            if (r.error) throw new Error(r.error);
            sp.textContent = nuevo;
            if (recargar) setTimeout(recargar, 400);
          } catch (e) {
            sp.textContent = original;
            alert('No se pudo guardar: ' + e.message);
          }
        };
        input.addEventListener('keydown', ev => {
          if (ev.key === 'Enter') { ev.preventDefault(); guardar(); }
          if (ev.key === 'Escape') { ev.preventDefault(); cancelar(); }
        });
        input.addEventListener('blur', guardar);
      });
    });
  }

  // ---------- paginación simple (solo para endpoints con {items,total,page}) ----------
  function paginador(cont, endpoint, aid, page, total, modoHumano, recargar) {
    const paginas = Math.max(1, Math.ceil((total || 0) / POR_PAG));
    if (paginas <= 1) return;
    const bar = document.createElement('div');
    bar.className = 'mh-pag';
    const prev = document.createElement('button'); prev.textContent = '◀'; prev.disabled = page <= 1;
    const info = document.createElement('span'); info.textContent = 'página ' + page + ' / ' + paginas + '  (' + total + ' items)';
    const next = document.createElement('button'); next.textContent = '▶'; next.disabled = page >= paginas;
    const ir = (p) => {
      let url = endpoint.replace(/[?&]page=\d+/, '');
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'page=' + p;
      render(cont, url, modoHumano, aid);
    };
    prev.onclick = () => ir(page - 1);
    next.onclick = () => ir(page + 1);
    bar.appendChild(prev); bar.appendChild(info); bar.appendChild(next);
    cont.insertBefore(bar, cont.firstChild);
  }

  // ---------- API: render ----------
  async function render(cont, endpoint, modoHumano, aid) {
    if (!modoHumano) return false;      // OFF: no tocamos nada (app.js pinta crudo)
    estilos();
    // el contenedor suele ser <pre> (white-space:pre-wrap): al meter tablas/fichas
    // heredaría ese formato y se rompe en móvil -> lo neutralizamos aquí.
    try { cont.style.whiteSpace = 'normal'; cont.style.wordBreak = 'normal'; } catch (e) {}
    cont.innerHTML = '<div class="mh-estado">Cargando en modo humano…</div>';
    let url = /[?&]humano=/.test(endpoint) ? endpoint : conHumano(endpoint);
    let d;
    try { d = await api(url); }
    catch (e) { cont.innerHTML = '<div class="mh-estado">⚠ Error: ' + esc(e.message) + '</div>'; return true; }

    // reintento por si el agente se resuelve a otro con datos
    const aidEf = aid || (function () {
      const m = /\/api\/memoria\/([^/?]+)/.exec(endpoint || ''); return m ? m[1] : '';
    })();

    const recargar = () => render(cont, endpoint, modoHumano, aidEf);

    // 1) pizarra con mensajes
    if (d && Array.isArray(d.mensajes)) {
      cont.innerHTML = mensajes(d.mensajes) +
        '<div class="mh-estado">Total ' + (d.total_lineas != null ? d.total_lineas : d.mensajes.length) + ' mensajes.</div>';
      return true;
    }

    // 2) PERSONA (submenú 👤): texto del persona.md como ficha bonita, no JSON/tabla
    if (tipoDe(endpoint) === 'persona' && d && Array.isArray(d.items) &&
        d.items.length && typeof d.items[0].texto === 'string') {
      cont.innerHTML = personaBonita(d.items[0]);
      return true;
    }

    // 2b) estructuras con items/total/page (memoria humanizada)
    if (d && Array.isArray(d.items)) {
      cont.innerHTML = '';
      if (d.total != null) paginador(cont, endpoint, aidEf, d.page || 1, d.total, modoHumano, recargar);
      const bloque = document.createElement('div');
      bloque.innerHTML = tabla(d.items, tipoDe(endpoint), d.editable || null, aidEf, recargar);
      cont.appendChild(bloque);
      if (d.editable && d.editable.length) activarEdicion(cont, aidEf, recargar);
      return true;
    }

    // 3) persona: {fuente, items:[{fuente,texto}]} ya viene como items -> caso 2
    // 4) otros: buscamos el primer array de objetos
    const arr = primeraLista(d);
    if (arr && arr.length && typeof arr[0] === 'object') {
      cont.innerHTML = tabla(arr, tipoDe(endpoint), (d && d.editable) || null, aidEf, recargar);
      if (d && d.editable && d.editable.length) activarEdicion(cont, aidEf, recargar);
      return true;
    }

    // 5) objeto suelto -> ficha clave/valor legible
    cont.innerHTML = ficha(d);
    return true;
  }

  function primeraLista(o) {
    if (!o || typeof o !== 'object') return null;
    const pref = ['items', 'entradas', 'agentes', 'pizarras', 'mensajes', 'datos', 'rows'];
    for (const k of pref) if (Array.isArray(o[k])) return o[k];
    for (const k in o) if (Array.isArray(o[k]) && o[k].length && typeof o[k][0] === 'object') return o[k];
    return null;
  }

  function ficha(o) {
    if (o == null) return '<div class="mh-estado">Sin datos.</div>';
    if (typeof o !== 'object') return '<div class="mh-estado">' + esc(String(o)) + '</div>';
    let h = '<div class="mh-scroll"><table class="mh-tabla">';
    Object.keys(o).forEach(k => {
      h += '<tr><th>' + esc(k) + '</th><td>' + celdaValor(k, o[k]) + '</td></tr>';
    });
    return h + '</table></div>';
  }

  window.ModoHumano = { render: render, version: '1.0' };
})();

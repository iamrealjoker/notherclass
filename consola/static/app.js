// app.js — front de la Superconsola NotherClass (JokerV1)
const $=s=>document.querySelector(s);
let menu=[],agentes=[],sel='joker',modoIA=true,vista='chat';
let modoHumano=false,ultimoEndpoint=null;

// ---------- utilidades ----------
function esc(t){return String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function jsonbonito(o){try{return JSON.stringify(o,null,2)}catch(e){return String(o)}}
async function api(url,opts){const r=await fetch(url,opts);if(!r.ok)throw new Error(r.status+' '+url);return r.json();}
function post(url,data){return api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}

// ---------- vista ----------
function mostrar(id,titulo){
  vista=id;
  document.querySelectorAll('.vista').forEach(v=>v.classList.remove('activa'));
  const v=$('#v_'+id);if(v)v.classList.add('activa');
  $('#titulo').textContent=titulo||id;
  document.querySelectorAll('.mitem,.msub').forEach(e=>e.classList.toggle('sel',e.dataset.vista===id));
}
function abrirDatos(id,url,titulo){
  mostrar(id,titulo);
  const pre=$('#datos');
  ultimoEndpoint=url;
  // Modo humano ON: capa legible (tablas, embeddings ocultos, edición segura)
  if(modoHumano&&window.ModoHumano){pre.innerHTML='';window.ModoHumano.render(pre,url,true,sel);return;}
  pre.textContent='Cargando…';
  api(url).then(d=>{pre.innerHTML=formatear(d)}).catch(e=>{pre.textContent='Error: '+e.message});
}
// render por tipo: tabla para arrays, tarjetas para listas conocidas, JSON para el resto
function formatear(o){
  const arr=Array.isArray(o)?o:(o&&Array.isArray(o.entradas)?o.entradas:(o&&Array.isArray(o.agentes)?o.agentes:null));
  if(arr&&arr.length&&typeof arr[0]==='object'){
    const claves=[...new Set(arr.flatMap(x=>Object.keys(x)))];
    let h='<table style="border-collapse:collapse;font-size:13px;width:100%">';
    h+='<tr>'+claves.map(k=>`<th style="text-align:left;padding:6px 10px;border-bottom:1px solid #30363d;color:#58a6ff">${esc(k)}</th>`).join('')+'</tr>';
    arr.forEach(f=>{h+='<tr>'+claves.map(k=>{
      let v=f[k];if(v&&typeof v==='object')v=JSON.stringify(v);
      const s=String(v??'');const largo=s.length>140?s.slice(0,140)+'…':s;
      return `<td style="padding:6px 10px;border-bottom:1px solid #21262d;vertical-align:top">${esc(largo)}</td>`;}).join('')+'</tr>';});
    return h+'</table>';
  }
  return '<pre style="font-family:ui-monospace,monospace;white-space:pre-wrap;word-break:break-word">'+esc(jsonbonito(o))+'</pre>';
}

// ---------- menú (desde /api/menu, backend) ----------
const VISTAS_FIJAS={
  chat:{vista:'chat',titulo:'Chat con agentes',fn:()=>{mostrar('chat','Chat con agentes');cargarChat();}},
  traducir:{vista:'traducir',titulo:'Traductor',fn:()=>mostrar('traducir','Traductor')},
};
function pintarMenu(){
  const box=$('#menu');box.innerHTML='';
  // selector de agente
  // descripción corta del agente bajo el selector (contenedor creado si falta)
  let dDesc=document.getElementById('desc_agente');
  if(!dDesc){dDesc=document.createElement('div');dDesc.id='desc_agente';
    dDesc.style.cssText='font-size:12px;opacity:.75;margin:2px 0 6px;color:#8b949e';
    box.appendChild(dDesc);}
  const selDiv=document.createElement('div');
  selDiv.className='mitem';selDiv.style.paddingBottom='6px';
  selDiv.innerHTML='<div style="font-size:12px;opacity:.6;margin-bottom:4px">Agente</div>'+
    '<select id="sel_agente" style="width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:6px">'+
    agentes.map(a=>`<option value="${esc(a.id)}" ${a.id===sel?'selected':''}>${esc(a.nombre||a.id)}</option>`).join('')+'</select>';
  selDiv.querySelector('#sel_agente').onchange=e=>{sel=e.target.value;mostrar(vista);cargarChat();};
  box.appendChild(selDiv);
  // entradas del backend
  menu.forEach(m=>{
    const el=document.createElement('div');
    el.className='mitem';el.dataset.vista=m.id;
    el.innerHTML=`${m.icono||'•'} ${esc(m.titulo)}${m.sub?'<span class="chev">▸</span>':''}`;
    const vf=VISTAS_FIJAS[m.id];
    el.onclick=()=>{
      if(m.sub){el.classList.toggle('abierto');return;}
      quizasCerrar();
      if(vf)return vf.fn();
      abrirDatos('datos',m.endpoint,m.titulo);
    };
    box.appendChild(el);
    (m.sub||[]).forEach(s=>{
      const se=document.createElement('div');
      se.className='msub';se.dataset.vista=s.id;
      se.textContent=`${s.icono||'·'} ${s.titulo}`;
      se.onclick=()=>{quizasCerrar();
        if(VISTAS_FIJAS[s.id])return VISTAS_FIJAS[s.id].fn();
        abrirDatos('datos',s.endpoint,s.titulo);
      };
      box.appendChild(se);
    });
  });
  document.querySelectorAll('.mitem,.msub').forEach(e=>e.classList.toggle('sel',e.dataset.vista===vista));
}

// ---------- burger: abrir/cerrar menú lateral (toggle estable) ----------
const burger=$('#burger'),sidebar=$('#sidebar');
const overlay=document.getElementById('overlay');
function pintarBurger(){burger.textContent=sidebar.classList.contains('oculto')?'☰':'✕';
  if(overlay)overlay.classList.toggle('vis',!sidebar.classList.contains('oculto')&&window.innerWidth<800);}
burger.onclick=()=>{sidebar.classList.toggle('oculto');pintarBurger();};
function cerrarSiAbierto(){if(!sidebar.classList.contains('oculto')){sidebar.classList.add('oculto');pintarBurger();}}
if(overlay)overlay.onclick=cerrarSiAbierto;
if(overlay)overlay.addEventListener('touchstart',e=>{e.preventDefault();cerrarSiAbierto();},{passive:false});
window.addEventListener('resize',pintarBurger);
window.addEventListener('orientationchange',()=>setTimeout(pintarBurger,120));
// si la página se abre en móvil (o se rota), el menú arranca SIEMPRE cerrado
if(window.innerWidth<800)sidebar.classList.add('oculto');
pintarBurger();
// cerrar el menú solo en móvil al elegir una vista (en PC se deja abierto)
function quizasCerrar(){if(window.innerWidth<800&&!sidebar.classList.contains('oculto')){sidebar.classList.add('oculto');pintarBurger();}}

// ---------- modo humano/IA ----------
$('#sw_modo').onclick=()=>{
  modoIA=!modoIA;
  $('#sw_modo').classList.toggle('on',!modoIA);
  $('#modo_txt').textContent=modoIA?'Modo IA':'Modo humano';
  $('#modo_lbl').textContent=modoIA?'modo agente (idioma IA)':'modo humano';
  modoHumano=!modoIA; // el toggle SIEMPRE activa la capa legible (antes no se activaba nunca)
  if(vista==='chat')cargarChat();
  else if(vista==='datos'&&ultimoEndpoint)abrirDatos('datos',ultimoEndpoint,$('#titulo').textContent);
};

// ---------- chat ----------
let hist=[];
async function cargarChat(){
  // refresco silencioso de agentes (por si se creó/eliminó alguno)
  api('/api/agentes').then(a=>{
    const nuevos=a.agentes||a||[];
    if(JSON.stringify(nuevos)!==JSON.stringify(agentes)){agentes=nuevos;if(!agentes.find(x=>x.id===sel))sel=agentes[0]?agentes[0].id:sel;pintarMenu();}
  }).catch(()=>{});
  $('#msgs').innerHTML='<div style="opacity:.5;font-size:13px">Cargando historial…</div>';
  try{
    const d=await api(`/api/chat/${sel}/historial`);
    hist=d.historial||d.mensajes||d||[];
  }catch(e){hist=[];}
  if(!hist.length)$('#msgs').innerHTML='<div style="opacity:.5;font-size:13px">Sin mensajes todavía. Escribe algo.</div>';
  else pintarMsgs();
  pintarDesc();
}
function pintarMsgs(){
  const box=$('#msgs');box.innerHTML='';
  hist.forEach(m=>{
    const who=m.rol||m.role||m.quien||'';
    const ia=String(who).toLowerCase().includes('ia')||String(who).toLowerCase().includes('agent')||String(who).toLowerCase()==='assistant';
    const d=document.createElement('div');
    d.className='m '+(ia?'ia':'humano');
    d.textContent=m.texto||m.contenido||m.content||jsonbonito(m);
    if(m.audio)anadirAudio(d,m.audio);
    if(Array.isArray(m.trazado)&&m.trazado.length)anadirEjecutado(d,m.trazado,m.seg);
    box.appendChild(d);
  });
  box.scrollTop=box.scrollHeight;
}
// Botón ▶ para reproducir un audio guardado (como las notas de voz de Telegram)
function anadirAudio(msgDiv,nombreAudio){
  if(!nombreAudio)return;
  // UN SOLO botón por mensaje: si ya existe (p. ej. llegó por TTS y también por historial), se reutiliza
  let b=msgDiv.querySelector('.audio-btn');
  if(!b){
    b=document.createElement('button');
    b.className='audio-btn';b.textContent='▶ voz';
    b.style.cssText='display:block;margin-top:4px;background:#21262d;color:#58a6ff;border:1px solid #30363d;border-radius:6px;padding:2px 8px;font-size:12px;cursor:pointer';
    msgDiv.appendChild(b);
  }
  if(b._audioNombre===nombreAudio&&b._player)return; // ya está cableado con ESTE audio
  b._audioNombre=nombreAudio;
  let a=null;
  const setTxt=t=>{b.textContent=t;};
  b.onclick=()=>{
    if(!a){
      a=new Audio(`/api/voz/audio/${sel}/${encodeURIComponent(nombreAudio)}`);
      b._player=a;
      a.onended=()=>{setTxt('▶ voz');a.currentTime=0;}; // al terminar, listo para repetir desde el principio
      a.onerror=()=>{setTxt('⚠ voz');};
      setTxt('⏸ voz');
      a.play().catch(()=>setTxt('▶ voz'));
      return;
    }
    if(a.paused){
      setTxt('⏸ voz');
      a.play().catch(()=>setTxt('▶ voz'));
    }else{
      a.pause(); // pausa de verdad: al volver a pulsar, CONTINÚA donde estaba (no reinicia)
      setTxt('▶ voz');
    }
  };
}
// Desplegable "⚙️ ejecutado": guarda la traza COMPLETA de lo que hizo el agente
// (herramientas + salidas, sin cortar). Clic = desplegar/cerrar. No se borra.
function anadirEjecutado(msgDiv,trazado,seg){
  if(!trazado||!trazado.length)return;
  const det=document.createElement('details');
  det.style.cssText='margin-top:6px;font-size:12px;border:1px solid #30363d;border-radius:8px;background:#0d1117;overflow:hidden';
  const sum=document.createElement('summary');
  sum.textContent=`⚙️ ejecutado (${trazado.length} paso${trazado.length===1?'':'s'}${seg?`, ${seg}s`:''})`;
  sum.style.cssText='cursor:pointer;padding:5px 9px;color:#8b949e;user-select:none';
  det.appendChild(sum);
  const pre=document.createElement('pre');
  pre.textContent=trazado.join('\n\n────────────────\n\n');
  pre.style.cssText='margin:0;padding:9px;white-space:pre-wrap;word-break:break-word;border-top:1px solid #21262d;color:#adbac7;max-height:320px;overflow:auto';
  det.appendChild(pre);
  msgDiv.appendChild(det);
}
$('#send').onclick=()=>enviar(false);
$('#txt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();enviar(false);}});
async function enviar(desdeVoz,textoOverride,audioOverride){
  const t=(textoOverride!==undefined?textoOverride:$('#txt').value).trim();if(!t)return;
  const nombreAudioHumano=audioOverride||''; // nota de voz del humano (persistida por /stt)
  $('#txt').value='';$('#send').disabled=true;
  const box=$('#msgs');
  const m=document.createElement('div');m.className='m humano';m.textContent=t;box.appendChild(m);
  const w=document.createElement('div');w.className='m ia';w.textContent='⏳ Pensando…';box.appendChild(w);
  box.scrollTop=box.scrollHeight;
  // ── ESTADO EN VIVO (EFÍMERO): mientras el agente trabaja pedimos su estado real
  //    cada 700 ms y lo pintamos en ESTA burbuja (qué herramienta usa, tiempo…).
  //    Al llegar la respuesta, la burbuja se reemplaza por el texto final.
  const t0=Date.now();
  const estadoTimer=setInterval(async()=>{
    try{
      const e=await api(`/api/chat/${sel}/estado`);
      if(e&&e.activo&&e.texto){
        const seg=Math.round((Date.now()-t0)/1000);
        w.textContent=e.texto+`\n\n⏱ ${seg}s`;
        box.scrollTop=box.scrollHeight;
      }
    }catch(_){}
  },700);
  let d=null,respR=null,err=null;
  for(let i=0;i<2&&!d;i++){try{d=await post(`/api/chat/${sel}`,modoIA&&(desdeVoz||vozOn)
    ?{texto:t,modo:'ia',audio_humano:nombreAudioHumano,audio_respuesta:'__TTS__'}
    :{texto:t,modo:modoIA?'ia':'humano',audio_humano:nombreAudioHumano});}catch(e){err=e;await new Promise(r=>setTimeout(r,800));}}
  clearInterval(estadoTimer);
  if(d){
    respR=d.respuesta||d.texto||jsonbonito(d);
    w.textContent=respR;
    // ── Desplegable "⚙️ ejecutado": lo que hizo el agente queda AQUÍ (no se borra).
    //    Clic para abrir/cerrar y poder trazar después lo que ejecutó.
    if(Array.isArray(d.trazado)&&d.trazado.length)anadirEjecutado(w,d.trazado,d.seg);
    if(nombreAudioHumano)anadirAudio(m,nombreAudioHumano); // nota de voz del HUMANO en SU mensaje (no en el del bot)
    // TTS: genera el audio (lo guarda el backend), lo reproduce al llegar y deja el botón para volver a escucharlo.
    if(modoIA&&(desdeVoz||vozOn))hablar(respR,desdeVoz,w);
  }
  else w.textContent='⚠ Error: '+(err?err.message:'sin respuesta')+' (reintentado)';
  $('#send').disabled=false;
}

// ---------- descripción corta del agente al abrir su chat ----------
async function pintarDesc(){
  const cont=$('#desc_agente');if(!cont)return;
  cont.textContent='';
  try{
    const d=await api(`/api/agente/${sel}`);
    const desc=d.descripcion||d.desc||'';
    if(desc){cont.textContent=desc;cont.style.display='block';}
    else cont.style.display='none';
  }catch(e){cont.style.display='none';}
}

// ---------- MODO VOZ (STT + TTS en BACKEND, motor real de los agentes) ----------
// STT: MediaRecorder -> POST /api/voz/<aid>/stt (multipart 'audio') -> {texto}
// TTS: POST /api/voz/<aid>/tts {texto} -> wav de piper, se reproduce con <audio>
let vozOn=false,grabando=false,mediaRec=null,chunks=[],stream=null;
let analizador=null,vadTimer=null,silencioDesde=0,vadBuf=null;
const micBtn=$('#mic'),micCont=$('#mic_cont');

function _vozAviso(msg){ // error VISIBLE, no solo tooltip
  micBtn.textContent='⚠';micBtn.title=msg;
  let t=document.getElementById('voz_aviso');
  if(!t){t=document.createElement('div');t.id='voz_aviso';
    t.style.cssText='background:#da3633;color:#fff;padding:6px 10px;border-radius:8px;font-size:12px;margin:4px 0';
    $('#txt').parentNode.insertBefore(t,$('#txt'));
    setTimeout(()=>t.remove(),8000);}
  t.textContent=msg;
}
async function _prepRec(){
  if(mediaRec)return true;
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
    _vozAviso('⚠ Sin acceso al micro: esta página se sirve por HTTP. Abre por localhost o HTTPS (p. ej. ngrok) para usar la voz.');return false;}
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true});
    mediaRec=new MediaRecorder(stream);
    mediaRec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    // Al parar la grabación transcribimos YA (antes: setTimeout de 150 ms extra).
    mediaRec.onstop=()=>{_stt();};
    return true;
  }catch(e){_vozAviso('⚠ Micrófono no disponible: '+e.message);return false;}
}
async function _stt(){
  if(!chunks.length)return;
  const blob=new Blob(chunks,{type:mediaRec.mimeType||'audio/webm'});chunks=[];
  const fd=new FormData();fd.append('audio',blob,'grabacion.webm');
  micBtn.textContent='⏳';
  // aviso EFÍMERO mientras se transcribe (se borra solo al llegar el texto)
  const aviso=document.createElement('div');aviso.className='m ia';
  aviso.textContent='🎤 transcribiendo…';
  $('#msgs').appendChild(aviso);$('#msgs').scrollTop=$('#msgs').scrollHeight;
  try{
    const d=await api(`/api/voz/${sel}/stt`,{method:'POST',body:fd});
    const t=(d.texto||'').trim();
    if(t){enviar(true,t,d.audio||'');}
    else micBtn.title='No se entendió nada (audio vacío)'; // en modo continuo el VAD sigue: no requiere pulsar nada
  }catch(e){_vozAviso('⚠ STT: '+e.message);}
  finally{aviso.remove();}
  const enc=vozOn?'🔴':'🎙';micBtn.textContent=enc;micBtn.title=vozOn?'Conversación activa (toca para parar)':'Conversación: toca una vez y habla';
  micCont.textContent=vozOn?'🔴':'🎧';micCont.classList.toggle('continuo',vozOn);
}
async function _arrancar(){
  if(!(await _prepRec()))return;
  grabando=true;micBtn.classList.add('escuchando');micBtn.textContent='🔴';
  if(mediaRec.state==='inactive')mediaRec.start();
}
function _parar(){
  grabando=false;micBtn.classList.remove('escuchando');
  try{mediaRec.stop();}catch(_){}   // al parar, onstop() llama a _stt(): sin esperas
}
// ---- Escucha continua (VAD por volumen): oye, y al detectar ~1.5s de silencio tras hablar, envía ----
async function _toggleContinuo(desdeMic){
  if(vozOn){ // apagar (solo el botón que la encendió apaga; el otro sincroniza)
    vozOn=false;micCont.style.background='#21262d';micCont.textContent='🎧';
    micBtn.classList.remove('escuchando');micBtn.textContent='🎙';
    clearInterval(vadTimer);vadTimer=null;silencioDesde=0;
    if(grabando)_parar();else if(mediaRec&&mediaRec.state==='recording')mediaRec.stop();
    try{stream.getTracks().forEach(t=>t.stop());}catch(_){}
    mediaRec=null;
    return;
  }
  if(desdeMic&&grabando)return; // ya está en marcha con el otro botón
  if(!(await _prepRec()))return;
  vozOn=true;micCont.style.background='#1f6feb';micCont.textContent='🔴';
  micBtn.classList.add('escuchando');micBtn.textContent='🔴'; // sincroniza el 🎙 con el 🎧
  // analizador de volumen para VAD
  try{
    const ac=new (window.AudioContext||window.webkitAudioContext)();
    const src=ac.createMediaStreamSource(stream);
    analizador=ac.createAnalyser();analizador.fftSize=512;src.connect(analizador);
    vadBuf=new Uint8Array(analizador.frequencyBinCount);
  }catch(_){analizador=null;}
  let hablando=false;
  // VAD: umbral de volumen y silencio para cortar. 1500 -> 900 ms: menos espera
  // tras dejar de hablar (era el principal lastre de velocidad de la web).
  const UMBRAL=12,MS_SILENCIO=900;
  clearInterval(vadTimer);
  vadTimer=setInterval(()=>{
    if(!vozOn||!analizador||ttsSonando)return;
    analizador.getByteTimeDomainData(vadBuf);
    let pico=0;for(let i=0;i<vadBuf.length;i++){const d=Math.abs(vadBuf[i]-128);if(d>pico)pico=d;}
    const ahora=Date.now();
    if(pico>UMBRAL){
      hablando=true;silencioDesde=0;
      if(!grabando){chunks=[];_arrancar();}
    }else if(hablando&&grabando){
      if(!silencioDesde)silencioDesde=ahora;
      if(ahora-silencioDesde>=MS_SILENCIO){hablando=false;silencioDesde=0;_parar();}
    }
  },120);
}
if(micBtn){
  // MODO CONVERSACIÓN: un toque activa/desactiva. No hay que mantener pulsado.
  micBtn.addEventListener('click',e=>{e.preventDefault();_desbloquearAudio();_toggleContinuo(true);});
  micBtn.addEventListener('contextmenu',e=>e.preventDefault());
}
if(micCont){micCont.addEventListener('click',()=>{_desbloquearAudio();_toggleContinuo(false);});}
let vozAudio=null,ttsSonando=false;
function _audioEl(){if(!vozAudio)vozAudio=new Audio();return vozAudio;}
// Desbloquea el autoplay del navegador (sobre todo en móvil) DENTRO del gesto del usuario.
// Un <audio> SIN src hace que play() se RECHACE y el navegador NO marca el elemento como
// desbloqueado (el audio posterior seguía bloqueado). Solución: reproducir un WAV
// silencioso real (data URI) durante el gesto; así play() SÍ resuelve y queda permitido.
const _WAV_SILENCIO='data:audio/wav;base64,UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';
function _desbloquearAudio(){
  try{const a=_audioEl();a.muted=true;a.src=_WAV_SILENCIO;
    const p=a.play();
    if(p&&p.then)p.then(()=>{a.muted=false;}).catch(()=>{a.muted=false;});
    else a.muted=false;
  }catch(_){}
}
function hablar(t,forzar,msgDiv){ // TTS backend (piper) -> reproduce y guarda el botón ▶ en el mensaje
  if(!t)return;
  if(!(forzar||vozOn))return;
  fetch(`/api/voz/${sel}/tts`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texto:t})})
    .then(r=>{
      if(!r.ok)throw 0;
      const nom=r.headers.get('X-Audio-Nombre'); // nombre del wav guardado en disco
      return r.blob().then(b=>({b,nom}));
    })
    .then(({b,nom})=>{
      const a=_audioEl();
      try{a.pause();}catch(_){}
      const u=URL.createObjectURL(b);
      a.src=u;
      a.onended=()=>{ttsSonando=false;try{URL.revokeObjectURL(u);}catch(_){}};
      a.onerror=()=>{ttsSonando=false;};
      ttsSonando=true; // mientras habla el bot, el VAD calla (no se oye a sí mismo)
      const p=a.play();
      if(p&&p.catch)p.catch(()=>{ // si el navegador aún lo bloquea, un reintento único
        setTimeout(()=>{try{const q=a.play();if(q&&q.catch)q.catch(()=>{ttsSonando=false;});}catch(_){ttsSonando=false;}},250);
      });
      // deja el audio reproducible en el mensaje (del historial o del recién llegado)
      const destino=msgDiv||document.querySelector('#msgs .m.ia:last-child');
      if(destino&&nom){
        anadirAudio(destino,nom);
        // persiste el nombre del audio del agente en el historial (POST #2);
        // así tras recargar el botón ▶ sigue ahí y el audio sigue sonando
        post(`/api/chat/${sel}/audio`,{indice:-1,nombre:nom}).catch(()=>{});
      }
    })
    .catch(()=>{ttsSonando=false;});
}
// ---------- traductor ----------
$('#tr_h').onclick=()=>trad('humano');
$('#tr_ia').onclick=()=>trad('ia');
async function trad(modo){
  const t=$('#tr_in').value.trim();if(!t)return;
  $('#tr_out').textContent='Traduciendo…';
  try{
    const d=await post(`/api/idioma/${sel}`,{texto:t,modo});
    $('#tr_out').textContent=d.traduccion||d.respuesta||d.texto||jsonbonito(d);
  }catch(e){$('#tr_out').textContent='⚠ Error: '+e.message;}
}

// ---------- init ----------
function cargarTodo(){
  Promise.all([api('/api/menu'),api('/api/agentes')])
    .then(([m,a])=>{
      menu=m.entradas||m||[];agentes=a.agentes||a||[];
      if(!agentes.find(x=>x.id===sel))sel=agentes[0]?agentes[0].id:sel;
      pintarMenu();cargarChat();
    })
    .catch(e=>{$('#menu').innerHTML='<div class="mitem" style="color:#f85149">⚠ '+e.message+'</div>';});
}
cargarTodo();

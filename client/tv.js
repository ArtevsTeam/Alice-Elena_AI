// ============================================================
// ELENA TV / MOBILE CLIENT
// ============================================================
const POLL_MS = 400;
const $ = id => document.getElementById(id);

let SERVER = null;
let state = { track: null, isPlaying: false, volume: 70, duration: 0 };
let lastTrackId = null;
let currentPlaylists = [];

// FIX: защита от наложения fetch-запросов
let polling = false;

// ==================== SERVER DISCOVERY ====================
function normalizeUrl(u){
  if(!u) return null;
  u = u.trim().replace(/\/+$/, '');
  if(!/^https?:\/\//.test(u)) u = 'http://' + u;
  return u;
}

async function probe(url, timeout=900){
  try{
    const ctrl = new AbortController();
    const t = setTimeout(()=>ctrl.abort(), timeout);
    const r = await fetch(url + '/api/info', {signal: ctrl.signal, cache:'no-store'});
    clearTimeout(t);
    if(!r.ok) return null;
    return await r.json();
  }catch(e){ return null; }
}

async function discoverServer(){
  setStatus('Ищу сервер в сети…');

  if(location.protocol.startsWith('http')){
    const info = await probe(location.origin, 1200);
    if(info){
      SERVER = location.origin;
      saveServer(SERVER);
      return true;
    }
  }

  const saved = normalizeUrl(localStorage.getItem('elena_server'));
  if(saved){
    const info = await probe(saved, 1200);
    if(info){
      SERVER = saved;
      return true;
    }
  }

  const subnetPrefixes = [
    '192.168.1.', '192.168.0.', '192.168.31.', '192.168.43.', '10.0.0.'
  ];
  const ports = [8765, 8443];
  const candidates = [];
  for(const prefix of subnetPrefixes){
    for(let i = 1; i <= 30; i++){
      for(const port of ports){
        const scheme = port === 8443 ? 'https' : 'http';
        candidates.push(`${scheme}://${prefix}${i}:${port}`);
      }
    }
  }

  setStatus(`Сканирую ${candidates.length} адресов…`);

  const BATCH = 60;
  for(let i = 0; i < candidates.length; i += BATCH){
    const slice = candidates.slice(i, i + BATCH);
    const results = await Promise.all(
      slice.map(async u => {
        const info = await probe(u, 600);
        return info ? u : null;
      })
    );
    const found = results.find(x => x);
    if(found){
      SERVER = found;
      saveServer(SERVER);
      return true;
    }
  }
  setStatus('Сервер не найден. Введи адрес вручную.');
  return false;
}

function saveServer(url){
  try{ localStorage.setItem('elena_server', url); }catch(e){}
}
function clearServer(){
  try{ localStorage.removeItem('elena_server'); }catch(e){}
  SERVER = null;
}
function setStatus(s){ $('conn-status').textContent = s; }

// ==================== API ====================
async function api(path, opts){
  const r = await fetch(SERVER + path, Object.assign({
    headers:{'Content-Type':'application/json'}, cache:'no-store'
  }, opts||{}));
  if(!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}
async function send(action, params={}){
  try{
    const j = await api('/api/cmd', {method:'POST',
      body: JSON.stringify({action, params})});
    if(j.reply && !j.play) toast(j.reply, 'reply');
    if(j.error) toast(j.error, 'err');
    if(j.play && j.play.track) applyPlay(j.play);
    if(j.state) applyState(j.state);
    return j;
  }catch(e){ toast('Нет связи с сервером', 'err'); }
}

// ==================== POLL ====================
// FIX: не запускаем новый запрос, если предыдущий ещё не завершён
async function poll(){
  if(polling) return;
  polling = true;
  try{
    const s = await api('/api/state');
    applyState(s);
  }catch(e){
    $('tv-server').textContent = '⚠ потерян';
  }finally{
    polling = false;
  }
}
function applyState(s){
  if(!s) return;
  if(s.volume != null){
    state.volume = s.volume;
    $('tv-vol').value = s.volume;
    $('tv-vol').style.setProperty('--p', s.volume + '%');
    $('tv-vol-val').textContent = s.volume;
  }
  if(s.track && s.track.id !== lastTrackId){
    lastTrackId = s.track.id;
    state.track = s.track;
    renderTrack();
  }
  if(typeof s.is_playing === 'boolean'){
    state.isPlaying = s.is_playing;
    renderPlayBtn();
  }
}
function applyPlay(play){
  if(!play || !play.track) return;
  lastTrackId = play.track.id;
  state.track = play.track;
  renderTrack();
}

// ==================== RENDER ====================
function proxyUrl(u){
  if(!u) return '';
  return SERVER + '/api/proxy_image?url=' + encodeURIComponent(u);
}

function renderTrack(){
  const t = state.track;
  if(!t) return;
  $('tv-track').textContent = t.title || '—';
  $('tv-artist').textContent = t.artists || '';
  const cov = t.cover_url ? proxyUrl(t.cover_url) : '';
  const el = $('tv-cover');
  if(cov){ el.style.backgroundImage = `url("${cov}")`; }
  else { el.style.backgroundImage = ''; }
  state.duration = t.duration || 0;
  $('tv-dur').textContent = fmtTime(state.duration);
}
function renderPlayBtn(){
  const playPath = '<path d="M7 5v14l12-7z"/>';
  const pausePath = '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>';
  $('tv-play-ico').innerHTML = state.isPlaying ? pausePath : playPath;
  $('tv-play-label').textContent = state.isPlaying ? 'Пауза' : 'Играть';
  $('tv-cover').classList.toggle('playing', state.isPlaying);
}
function fmtTime(s){
  s = Math.max(0, Math.floor(s||0));
  return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
}

function toast(text, kind=''){
  if(!text) return;
  const el = document.createElement('div');
  el.className = 'tv-toast ' + kind;
  el.textContent = text;
  $('tv-toasts').appendChild(el);
  setTimeout(()=>{ el.classList.add('out'); setTimeout(()=>el.remove(),300); }, 2500);
}

// ==================== VOICE (Web Speech API) ====================
let recognizer = null;
let voiceActive = false;

function startVoice(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){
    toast('Голосовой ввод недоступен на этом устройстве', 'err');
    return;
  }
  voiceActive = true;
  $('voice-overlay').classList.add('on');
  $('voice-text').textContent = 'Слушаю…';

  recognizer = new SR();
  recognizer.lang = 'ru-RU';
  recognizer.interimResults = true;
  recognizer.continuous = false;
  recognizer.maxAlternatives = 1;

  let finalText = '';
  recognizer.onresult = (e) => {
    let interim = '';
    for(let i = e.resultIndex; i < e.results.length; i++){
      const r = e.results[i];
      if(r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    $('voice-text').textContent = finalText || interim || 'Слушаю…';
  };
  recognizer.onerror = (e) => {
    if(e.error !== 'no-speech' && e.error !== 'aborted')
      toast('Ошибка распознавания: ' + e.error, 'err');
    stopVoice();
  };
  recognizer.onend = async () => {
    $('voice-overlay').classList.remove('on');
    voiceActive = false;
    const text = (finalText || '').trim();
    if(!text) return;
    await sendCommandText(text);
  };
  try { recognizer.start(); }
  catch(e){ stopVoice(); }
}
function stopVoice(){
  if(recognizer){ try{ recognizer.stop(); }catch(e){} }
  $('voice-overlay').classList.remove('on');
  voiceActive = false;
}
async function sendCommandText(text){
  toast('Вы: ' + text);
  try{
    const r = await api('/api/voice_text', {method:'POST',
      body: JSON.stringify({text})});
    if(r.text) toast('Вы: ' + r.text);
    if(r.reply) toast(r.reply, 'reply');
    if(r.play && r.play.track) applyPlay(r.play);
  }catch(e){
    toast('Ошибка обработки команды', 'err');
  }
}

// ==================== PLAYLISTS ====================
async function openPlaylistsModal(){
  $('playlists-modal').classList.add('on');
  const list = $('pl-list');
  list.innerHTML = '<div style="color:#8d8da0;padding:20px;text-align:center">Загрузка…</div>';
  try{
    const data = await api('/api/playlists');
    currentPlaylists = Array.isArray(data) ? data : [];
    list.innerHTML = '';
    if(!currentPlaylists.length){
      list.innerHTML = '<div style="color:#5a5a6e;padding:20px;text-align:center">Нет плейлистов</div>';
      return;
    }
    currentPlaylists.forEach(pl => {
      const el = document.createElement('div');
      el.className = 'pl-item';
      el.tabIndex = 0;
      const cov = pl.cover_url ? proxyUrl(pl.cover_url) : '';
      el.innerHTML = `
        <div class="pl-item-cover" ${cov?`style="background-image:url('${cov}')"`:''}></div>
        <div class="pl-item-name">${esc(pl.title)}</div>
        <div class="pl-item-count">${pl.track_count||0}</div>`;
      el.onclick = () => {
        closePlaylistsModal();
        send('play_playlist_track', {kind: pl.kind, index: 0});
      };
      list.appendChild(el);
    });
    setTimeout(()=>{ const first = list.querySelector('.pl-item'); if(first) first.focus(); }, 60);
  }catch(e){
    list.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center">Ошибка загрузки</div>';
  }
}
function closePlaylistsModal(){ $('playlists-modal').classList.remove('on'); }

// ==================== EVENTS ====================
function bind(){
  $('btn-rescan').onclick = async () => {
    clearServer();
    const ok = await discoverServer();
    if(ok) enterMain();
  };
  $('btn-manual').onclick = () => {
    $('conn-manual').classList.add('show');
    $('server-url').focus();
  };
  $('btn-connect').onclick = async () => {
    const u = normalizeUrl($('server-url').value);
    if(!u){ setStatus('Введи адрес'); return; }
    setStatus('Проверяю…');
    const info = await probe(u, 2000);
    if(info){
      SERVER = u;
      saveServer(SERVER);
      enterMain();
    } else {
      setStatus('Не удалось подключиться к ' + u);
    }
  };
  $('server-url').addEventListener('keydown', e => {
    if(e.key === 'Enter') $('btn-connect').click();
  });

  $('btn-change-server').onclick = () => {
    if(!confirm('Сменить сервер?')) return;
    clearServer();
    $('main-screen').classList.remove('active');
    $('connect-screen').classList.add('active');
    $('conn-status').textContent = 'Ищу сервер…';
    discoverServer();
  };

  $('tv-prev').onclick = () => send('previous');
  $('tv-next').onclick = () => send('next');
  $('tv-play').onclick = () => send('toggle');
  $('tv-voice').onclick = startVoice;
  $('tv-wave').onclick = () => send('my_wave');
  $('chip-liked').onclick = () => send('play_liked');
  $('chip-playlists').onclick = () => openPlaylistsModal();
  $('pl-close').onclick = closePlaylistsModal;

  document.querySelectorAll('.tv-chip[data-wave]').forEach(b => {
    b.onclick = () => send('my_wave', {seeds: [b.dataset.wave]});
  });

  const volEl = $('tv-vol');
  let volTimer = null;
  volEl.addEventListener('input', () => {
    const v = parseInt(volEl.value);
    volEl.style.setProperty('--p', v + '%');
    $('tv-vol-val').textContent = v;
    clearTimeout(volTimer);
    volTimer = setTimeout(() => send('volume_set', {level: v}), 150);
  });

  document.addEventListener('keydown', e => {
    const inInput = document.activeElement && document.activeElement.matches('input');
    if(inInput) return;
    if(e.key === 'Escape'){
      if($('playlists-modal').classList.contains('on')) closePlaylistsModal();
      else if(voiceActive) stopVoice();
      else if(document.fullscreenElement) document.exitFullscreen();
      return;
    }
    if(e.key === 'MediaPlayPause'){ send('toggle'); e.preventDefault(); }
    if(e.key === 'MediaTrackNext'){ send('next'); e.preventDefault(); }
    if(e.key === 'MediaTrackPrevious'){ send('previous'); e.preventDefault(); }
    if(e.key === 'MediaStop'){ send('pause'); e.preventDefault(); }
  });

  $('tv-cover').addEventListener('click', () => send('toggle'));
}

// ==================== ESCAPE HELPER ====================
function esc(s){
  return String(s||'').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ==================== ENTER MAIN ====================
function enterMain(){
  $('connect-screen').classList.remove('active');
  $('main-screen').classList.add('active');
  $('tv-server').textContent = SERVER.replace(/^https?:\/\//, '');
  poll();
  setInterval(poll, POLL_MS);
  setTimeout(()=>$('tv-play').focus(), 200);
}

// ==================== INIT ====================
async function init(){
  bind();
  const ok = await discoverServer();
  if(ok) enterMain();
  else $('conn-manual').classList.add('show');
}
window.addEventListener('DOMContentLoaded', init);
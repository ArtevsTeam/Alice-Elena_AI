// ============================================================
// ELENA CLIENT v7.1
// ============================================================
const POLL_MS = 350;
const state = {
  track: null, isPlaying: false, volume: 70, position: 0, duration: 0,
  eqEnabled: false, eqPreset: 'flat',
  playlists: [], playlistsLoaded: false,
  likedTracks: [], likedLoaded: false, likedLoading: false,
  isSeeking: false, lastTrackId: null, endedSent: null,
  isSpeaking: false, alwaysListen: false,
  currentView: 'library',
  artistCache: {},
  wave: null, repeat: false, isLiked: false,
  queueOpen: false, queueTimer: null,
};

let lastActivation = 0;
let beepCtx = null;
let decodeCtx = null;

const $ = id => document.getElementById(id);
const audio = $('audio');

const WAVE_MOODS = [
  { seed: 'genre:allrock',   emoji: '🎸', label: 'Рок' },
  { seed: 'genre:pop',       emoji: '🎤', label: 'Поп' },
  { seed: 'genre:hiphop',    emoji: '🎧', label: 'Хип-хоп' },
  { seed: 'genre:jazz',      emoji: '🎷', label: 'Джаз' },
  { seed: 'genre:classical', emoji: '🎻', label: 'Классика' },
  { seed: 'genre:metal',     emoji: '🤘', label: 'Метал' },
  { seed: 'genre:indie',     emoji: '🌿', label: 'Инди' },
  { seed: 'genre:electro',   emoji: '🎹', label: 'Электроника' },
];

// ==================== PROXY HELPER ====================
function proxyUrl(u){
  if(!u) return '';
  if(u.startsWith('/api/')) return u;
  return `/api/proxy_image?url=${encodeURIComponent(u)}`;
}

// ==================== AUDIO GRAPH ====================
let audioCtx, srcNode, analyser, eqFilters = [];
const EQ_PRESETS = {
  flat:[0,0,0,0,0,0,0,0,0,0], rock:[5,4,-1,-3,-2,2,5,6,6,5],
  pop:[-1,-1,2,4,5,3,-1,-1,-1,-1], jazz:[0,3,4,2,1,2,3,3,3,3],
  classical:[0,0,0,0,0,0,-3,-3,-3,-5], bass_boost:[9,7,5,3,0,0,0,0,0,0],
  vocal:[-3,-2,0,3,5,5,4,2,1,0], loudness:[6,4,0,-2,-1,2,5,5,5,5],
  electronic:[4,3,1,0,-2,2,1,4,5,5],
};
const EQ_FREQS = [60,170,310,600,1000,3000,6000,12000,14000,16000];

function initAudioGraph(){
  if(audioCtx) return;
  try{
    const Ctor = window.AudioContext||window.webkitAudioContext;
    audioCtx = new Ctor({ latencyHint: 'playback' });
    srcNode = audioCtx.createMediaElementSource(audio);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.78;
    analyser.minDecibels = -85; analyser.maxDecibels = -20;
    let node = srcNode;
    for(let i=0;i<EQ_FREQS.length;i++){
      const f=audioCtx.createBiquadFilter();
      f.type='peaking'; f.frequency.value=EQ_FREQS[i]; f.Q.value=1; f.gain.value=0;
      node.connect(f); node=f; eqFilters.push(f);
    }
    node.connect(analyser); analyser.connect(audioCtx.destination);
    drawSpectrum();
  }catch(e){ console.warn('audio graph', e); }
}
function applyEQ(enabled, preset){
  if(!audioCtx) return;
  const g = enabled ? (EQ_PRESETS[preset]||EQ_PRESETS.flat) : EQ_PRESETS.flat;
  eqFilters.forEach((f,i)=>f.gain.setTargetAtTime(g[i]||0, audioCtx.currentTime, .05));
}

// ==================== BEEP / ANIM ====================
function playBeep(){
  try{
    if(!beepCtx) beepCtx = new (window.AudioContext||window.webkitAudioContext)();
    if(beepCtx.state==='suspended') beepCtx.resume();
    const now = beepCtx.currentTime;
    [[880,0.0],[1318.51,0.08]].forEach(([freq,off])=>{
      const osc = beepCtx.createOscillator(); const g = beepCtx.createGain();
      osc.type='sine'; osc.frequency.value=freq;
      g.gain.setValueAtTime(0, now+off);
      g.gain.linearRampToValueAtTime(0.25, now+off+0.01);
      g.gain.exponentialRampToValueAtTime(0.001, now+off+0.16);
      osc.connect(g); g.connect(beepCtx.destination);
      osc.start(now+off); osc.stop(now+off+0.2);
    });
  }catch(e){}
}
function playActivationAnimation(){
  const btn = $('btn-mic'); if(!btn) return;
  btn.classList.add('rec');
  playBeep();
  setTimeout(()=>btn.classList.remove('rec'), 1600);
}
function playWaveAnimation(){
  const el = $('wave-anim'); if(!el) return;
  el.classList.remove('on');
  void el.offsetWidth;
  el.classList.add('on');
  setTimeout(()=>el.classList.remove('on'), 2100);
}

// ==================== SPECTRUM ====================
const canvas = $('spectrum'), ctx2 = canvas.getContext('2d');
const NB = 40;
let smoothBars = new Array(NB).fill(0), peakBars = new Array(NB).fill(0), freqData;
function resizeCanvas(){
  const dpr = devicePixelRatio||1;
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, r.width*dpr); canvas.height = Math.max(1, r.height*dpr);
}
addEventListener('resize', resizeCanvas);
function drawSpectrum(){
  requestAnimationFrame(drawSpectrum);
  if(!analyser || document.hidden) return;
  if(!freqData || freqData.length!==analyser.frequencyBinCount)
    freqData = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(freqData);
  const bins=freqData.length, sr=audioCtx?audioCtx.sampleRate:48000, nyq=sr/2;
  const minF=40,maxF=16000, bands=[];
  for(let i=0;i<NB;i++){
    const f0=minF*Math.pow(maxF/minF,i/NB), f1=minF*Math.pow(maxF/minF,(i+1)/NB);
    const b0=Math.max(0,Math.floor(f0/nyq*bins)), b1=Math.min(bins,Math.ceil(f1/nyq*bins));
    let s=0,c=0;
    for(let b=b0;b<b1;b++){ s+=freqData[b]; c++; }
    bands.push(c>0?Math.pow((s/c)/255,0.8):0);
  }
  for(let i=0;i<NB;i++){
    const t=bands[i];
    smoothBars[i]+= (t>smoothBars[i]?(t-smoothBars[i])*.55:(t-smoothBars[i])*.15);
    if(smoothBars[i]>peakBars[i]) peakBars[i]=smoothBars[i];
    else peakBars[i]=Math.max(0,peakBars[i]-.008);
  }
  renderSpectrum();
}
function renderSpectrum(){
  const W=canvas.width, H=canvas.height;
  ctx2.clearRect(0,0,W,H);
  const gap=2*(devicePixelRatio||1);
  const bw=(W-gap*(NB-1))/NB, halfH=H*0.72;
  for(let i=0;i<NB;i++){
    const v=smoothBars[i], bh=Math.max(2,v*halfH);
    const x=i*(bw+gap), y=halfH-bh;
    const grad=ctx2.createLinearGradient(0,y,0,halfH);
    grad.addColorStop(0,'#e879f9'); grad.addColorStop(.5,'#a855f7'); grad.addColorStop(1,'#7c3aed');
    ctx2.fillStyle=grad;
    ctx2.shadowColor='rgba(168,85,247,0.6)'; ctx2.shadowBlur=10*(devicePixelRatio||1);
    roundRect(ctx2,x,y,bw,bh,2*(devicePixelRatio||1)); ctx2.fill();
    ctx2.shadowBlur=0;
    const py=halfH-peakBars[i]*halfH;
    ctx2.fillStyle='rgba(255,255,255,0.7)'; ctx2.fillRect(x,py-1,bw,2);
    if(bh>4){
      const rh=bh*.3;
      const rg=ctx2.createLinearGradient(0,halfH+2,0,halfH+2+rh);
      rg.addColorStop(0,'rgba(168,85,247,0.4)'); rg.addColorStop(1,'rgba(168,85,247,0)');
      ctx2.fillStyle=rg;
      roundRect(ctx2,x,halfH+2,bw,rh,2*(devicePixelRatio||1)); ctx2.fill();
    }
  }
}
function roundRect(c,x,y,w,h,r){
  if(h<2*r) r=h/2;
  c.beginPath(); c.moveTo(x+r,y);
  c.arcTo(x+w,y,x+w,y+h,r); c.arcTo(x+w,y+h,x,y+h,r);
  c.arcTo(x,y+h,x,y,r); c.arcTo(x,y,x+w,y,r); c.closePath();
}

// ==================== API ====================
async function api(path, opts){
  const r = await fetch(path, Object.assign({
    headers:{'Content-Type':'application/json'}, cache:'no-store'
  }, opts||{}));
  if(!r.ok) throw new Error('HTTP '+r.status);
  return await r.json();
}
async function send(action, params={}){
  try{
    const j = await api('/api/cmd', {method:'POST', body: JSON.stringify({action, params})});
    if(!j.ok){ showToast(j.error||'Ошибка','err'); return j; }
    if(j.reply && !j.play) showToast(j.reply,'reply');
    if(j.play) playFromServer(j.play);
    if(j.state) applyState(j.state);
    if(typeof j.seek_pos === 'number' && !state.isSeeking){
      audio.currentTime = j.seek_pos;
      state.position = j.seek_pos;
      updateTimelineUI(j.seek_pos, state.duration || audio.duration || 0);
    }
    return j;
  }catch(e){ showToast('Нет связи с сервером','err'); }
}

// ==================== STATE ====================
async function pollState(){
  try{
    const s = await api('/api/state');
    applyState(s);
    setServerStatus(true);
  }catch(e){ setServerStatus(false); }
}
function setServerStatus(ok){
  const el = $('acc-btn'); if(el) el.title = ok ? 'Онлайн' : 'Нет связи';
}
function applyState(s){
  if(!s) return;
  state.isSpeaking = !!s.is_speaking;
  state.wave = s.wave || null;
  state.repeat = !!s.repeat;
  state.isLiked = !!s.is_liked;
  if(s.assistant_name){
    const at = $('assistant-title');
    if(at && at.textContent !== s.assistant_name) at.textContent = s.assistant_name;
  }
  renderLikeBtn();
  renderRepeatBtn();
  renderWaveBar();
  renderDlBadge(s.download);

  if(s.activation_ts && s.activation_ts > lastActivation + 0.3){
    const age = Date.now()/1000 - s.activation_ts;
    if(age < 5) playActivationAnimation();
    lastActivation = s.activation_ts;
  }

  if(s.track && s.track.id !== state.lastTrackId){
    state.lastTrackId = s.track.id;
    state.track = s.track;
    renderTrack();
    loadStream(s);
  } else if(s.track) state.track = s.track;

  if(typeof s.is_playing === 'boolean'){
    if(s.is_playing && audio.paused && audio.src && !audio.ended){
      if(audioCtx && audioCtx.state==='suspended') audioCtx.resume();
      audio.play().catch(()=>{});
    } else if(!s.is_playing && !audio.paused) audio.pause();
    state.isPlaying = s.is_playing;
    renderPlayBtn();
    const np = $('np'); if(np) np.classList.toggle('playing', s.is_playing);
  }

  if(typeof s.position === 'number' && !state.isSeeking && audio.src && !audio.paused){
    const diff = Math.abs(audio.currentTime - s.position);
    if(diff > 2.5 && isFinite(s.position)){
      audio.currentTime = s.position;
    }
  }

  if(typeof s.volume === 'number' && s.volume !== state.volume) setVolume(s.volume, false);
  if(typeof s.eq_preset === 'string') state.eqPreset = s.eq_preset;
  if(typeof s.eq_enabled === 'boolean') state.eqEnabled = s.eq_enabled;
  updateEqButton();
}
function renderLikeBtn(){
  const b = $('btn-like'); if(!b) return;
  b.classList.toggle('liked', state.isLiked);
}
function renderRepeatBtn(){
  const b = $('btn-repeat'); if(!b) return;
  b.classList.toggle('on', state.repeat);
}
function renderWaveBar(){
  const bar = $('wave-bar'); if(!bar) return;
  const on = !!state.wave;
  bar.classList.toggle('on', on);
  if(on){
    const st = state.wave.station || '';
    $('wb-station').textContent = st === 'user:onmywave' ? '🌊 Моя волна' : ('🌊 ' + st);
  }
}
function renderDlBadge(dl){
  const el = $('dl-badge'); if(!el) return;
  if(dl && dl.active && dl.total > 0){
    const pct = Math.min(100, Math.round((dl.done/dl.total)*100));
    el.classList.add('on');
    $('dl-name').textContent = pct + '%';
  } else {
    el.classList.remove('on');
  }
}

function renderTrack(){
  const t = state.track; if(!t) return;
  $('np-title').textContent = t.title || '—';
  $('np-artist').textContent = t.artists || '—';
  $('np-album').textContent = t.album ? `Альбом · ${t.album}` : '';
  $('uc-title').textContent = t.title || '—';
  $('uc-artist').textContent = t.artists || '';
  const url = t.cover_url ? proxyUrl(t.cover_url) : '';
  $('np-cover').style.backgroundImage = url?`url("${url}")`:'';
  $('uc-cover').style.backgroundImage = url?`url("${url}")`:'';
  const bg = $('bg-blur');
  if(bg){
    if(url){ bg.style.backgroundImage = `url("${url}")`; bg.classList.add('active'); }
    else bg.classList.remove('active');
  }
  applyCoverGlow(t.cover_url);
  state.duration = t.duration || 0;
  $('dur-t').textContent = fmtTime(state.duration);
  // FIX: синхронизируем сердечки в списках треков при смене трека
  syncLikes();
}

// FIX: обновляем все видимые «сердечки» в списках треков
function syncLikes(){
  const curId = state.track ? String(state.track.id) : null;
  document.querySelectorAll('.track-row').forEach(row=>{
    const lid = row.dataset.trackId;
    const like = row.querySelector('.m-like');
    if(!like) return;
    const isCur = curId && String(lid) === curId;
    if(isCur) like.classList.toggle('liked', state.isLiked);
  });
}

function applyCoverGlow(rawUrl){
  const wrap = document.querySelector('.np-cover-wrap');
  if(!wrap) return;
  if(!rawUrl){ wrap.style.setProperty('--glow-color','var(--grad)'); return; }
  const proxy = proxyUrl(rawUrl);
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    try{
      const c = document.createElement('canvas');
      c.width = 40; c.height = 40;
      const ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0, 40, 40);
      const d = ctx.getImageData(0,0,40,40).data;
      let r=0,g=0,b=0,n=0;
      for(let i=0;i<d.length;i+=4){
        const br = (d[i]+d[i+1]+d[i+2])/3;
        if(br < 25 || br > 235) continue;
        r+=d[i]; g+=d[i+1]; b+=d[i+2]; n++;
      }
      if(!n){ wrap.style.setProperty('--glow-color','var(--grad)'); return; }
      r=Math.round(r/n); g=Math.round(g/n); b=Math.round(b/n);
      const mx=Math.max(r,g,b), mn=Math.min(r,g,b), avg=(mx+mn)/2;
      const boost = v => Math.round(Math.max(0, Math.min(255, avg + (v-avg)*1.35)));
      r=boost(r); g=boost(g); b=boost(b);
      wrap.style.setProperty('--glow-color',
        `radial-gradient(circle, rgb(${r},${g},${b}) 0%, ` +
        `rgba(${r},${g},${b},.45) 55%, transparent 80%)`);
    }catch(e){
      wrap.style.setProperty('--glow-color','var(--grad)');
    }
  };
  img.onerror = ()=> wrap.style.setProperty('--glow-color','var(--grad)');
  img.src = proxy;
}

let lastStreamTrack = null;
async function loadStream(s){
  if(!audioCtx) initAudioGraph();
  if(audioCtx && audioCtx.state==='suspended') try{ await audioCtx.resume(); }catch(e){}
  const tid = s.track ? s.track.id : null;
  if(tid && tid === lastStreamTrack && audio.src) return;
  lastStreamTrack = tid;
  audio.src = `/api/stream?ts=${Date.now()}`;
  try{ audio.currentTime = s.position || 0; }catch(e){}
  if(s.is_playing) audio.play().catch(()=>{});
}
function renderPlayBtn(){
  const playPath  = '<path d="M7 5v14l12-7z"/>';
  const pausePath = '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>';
  const ico = $('play-ico');
  if(ico) ico.innerHTML = state.isPlaying ? pausePath : playPath;
  const uc = $('uc-play-ico');
  if(uc) uc.innerHTML = state.isPlaying ? pausePath : playPath;
  const card = $('user-card');
  if(card) card.classList.toggle('playing', state.isPlaying);
}
function updateVolUI(v){
  const el = $('vol'); if(!el) return;
  el.value = v; el.style.setProperty('--p', v+'%');
}
function setVolume(v, toServer=true){
  state.volume = v;
  audio.volume = Math.max(0, Math.min(1, v/100));
  updateVolUI(v);
  if(toServer) send('volume_set', {level: v});
}
function updateTimelineUI(pos, dur){
  if(dur <= 0) return;
  const pct = Math.min(100, Math.max(0, (pos/dur)*100));
  const p = $('progress');
  p.value = Math.min(1000, Math.round(pct*10));
  p.style.setProperty('--p', pct + '%');
  $('cur-t').textContent = fmtTime(pos);
}

function playFromServer(play){
  if(!play) return;
  if(play.track){
    state.track = play.track;
    state.lastTrackId = play.track.id;
    lastStreamTrack = play.track.id;
    renderTrack();
  }
  if(play.stream_url){
    audio.src = play.stream_url + '&ts=' + Date.now();
    audio.currentTime = play.position || 0;
    audio.play().catch(()=>{});
  }
}

// ==================== LIKES ====================
async function toggleLike(trackId){
  const willLike = !state.isLiked || String(trackId) !== String(state.track?.id);
  try{
    const r = await api('/api/like', {method:'POST',
      body: JSON.stringify({track_id: trackId, like: willLike})});
    if(r.ok){
      if(String(trackId) === String(state.track?.id)){
        state.isLiked = willLike;
        renderLikeBtn();
      }
      showToast(willLike ? '❤ В любимые' : 'Убрано из любимых');
      const b = $('btn-like');
      if(b){ b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }
      syncLikes();
    }
  }catch(e){ showToast('Ошибка лайка','err'); }
}

// ==================== QUEUE ====================
function openQueue(){
  state.queueOpen = true;
  $('queue-back').classList.add('open');
  $('queue-panel').classList.add('open');
  refreshQueue();
  clearInterval(state.queueTimer);
  state.queueTimer = setInterval(refreshQueue, 2000);
}
function closeQueue(){
  state.queueOpen = false;
  $('queue-back').classList.remove('open');
  $('queue-panel').classList.remove('open');
  clearInterval(state.queueTimer);
}
async function refreshQueue(){
  if(!state.queueOpen) return;
  try{
    const list = await api('/api/queue');
    const body = $('queue-body');
    body.innerHTML = '';
    if(!list.length){
      body.innerHTML = '<div class="empty">Очередь пуста</div>';
      return;
    }
    list.forEach(t => {
      const row = document.createElement('div');
      row.className = 'q-row' + (t.current ? ' current' : '');
      const cov = t.cover_url ? `background-image:url('${proxyUrl(t.cover_url)}')` : '';
      row.innerHTML = `
        <span class="q-num">${t.current ? '♪' : (t.index+1)}</span>
        <div class="q-cov" style="${cov}"></div>
        <div style="min-width:0">
          <div class="q-title">${esc(t.title)}</div>
          <div class="q-artist">${esc(t.artists)}</div>
        </div>
        <span class="q-dur">${fmtTime(t.duration)}</span>
        <button class="q-rm" title="Убрать">✕</button>`;
      row.onclick = async (e) => {
        if(e.target.classList.contains('q-rm')) return;
        const r = await api('/api/queue/play', {method:'POST',
          body: JSON.stringify({index: t.index})});
        if(r.play) playFromServer(r.play);
      };
      row.querySelector('.q-rm').onclick = async () => {
        await api('/api/queue/remove', {method:'POST',
          body: JSON.stringify({index: t.index})});
        refreshQueue();
      };
      body.appendChild(row);
    });
  }catch(e){}
}

// ==================== LYRICS ====================
async function openLyrics(){
  $('lyrics-back').classList.add('open');
  $('lyrics-title').textContent = state.track
    ? `${state.track.artists} — ${state.track.title}` : 'Текст песни';
  $('lyrics-text').textContent = 'Загрузка…';
  try{
    const r = await api('/api/lyrics');
    $('lyrics-text').textContent = r.lyrics ? r.lyrics.text
      : 'Текст недоступен (нужна подписка Плюс или у трека нет текста)';
  }catch(e){
    $('lyrics-text').textContent = 'Не удалось загрузить текст';
  }
}

// ==================== WAVE SETTINGS ====================
function openWaveSettings(){
  const st = (state.wave && state.wave.settings) || {};
  $('wv-mood').value = st.mood || '';
  $('wv-div').value = st.diversity || '';
  $('wv-lang').value = st.language || '';
  $('wave-back').classList.add('open');
}
async function saveWaveSettings(){
  const mood = $('wv-mood').value || null;
  const diversity = $('wv-div').value || null;
  const language = $('wv-lang').value || null;
  await api('/api/wave/settings', {method:'POST',
    body: JSON.stringify({mood, diversity, language})});
  $('wave-back').classList.remove('open');
  showToast('Настройки волны применены');
}

// ==================== PLAYLISTS ====================
async function loadPlaylists(){
  const body = $('playlists-body'); if(!body) return;
  body.innerHTML = '<div class="empty">Загрузка…</div>';
  try{
    const data = await api('/api/playlists');
    state.playlists = Array.isArray(data)?data:[];
    state.playlistsLoaded = true;
    renderPlaylists();
  }catch(e){
    body.innerHTML = '<div class="empty">Ошибка загрузки</div>';
    console.warn(e);
  }
}
function renderPlaylists(){
  const body = $('playlists-body'); if(!body) return;
  body.innerHTML = '';
  if(!state.playlists.length){
    body.innerHTML = '<div class="empty">Нет плейлистов</div>'; return;
  }
  state.playlists.forEach(pl=>{
    const btn = document.createElement('button');
    btn.className = 'chan chan-pl';
    const cover = pl.cover_url
      ? `style="background-image:url('${proxyUrl(pl.cover_url)}')"`
      : '';
    btn.innerHTML = `<div class="chan-pl-cover" ${cover}></div>
                     <span class="chan-name">${esc(pl.title)}</span>
                     <span class="chan-count">${pl.track_count||0}</span>`;
    btn.onclick = () => openPlaylist(pl.kind);
    body.appendChild(btn);
  });
}

// ==================== FOR YOU ====================
async function loadForYou(){
  const body = $('foryou-body'); if(!body) return;
  try{
    const data = await api('/api/landing');
    body.innerHTML = '';
    (Array.isArray(data)?data:[]).slice(0,6).forEach(pl=>{
      const btn = document.createElement('button');
      btn.className = 'chan chan-pl';
      const cover = pl.cover_url
        ? `style="background-image:url('${proxyUrl(pl.cover_url)}')"`
        : '';
      btn.innerHTML = `<div class="chan-pl-cover" ${cover}></div>
                       <span class="chan-name">${esc(pl.title)}</span>`;
      btn.title = pl.description || pl.title;
      btn.onclick = async () => {
        showToast('Включаю «' + pl.title + '»','reply');
        try{
          const r = await api('/api/play_by_id', {method:'POST',
            body: JSON.stringify({kind: pl.kind, uid: pl.uid})});
          if(r.play) playFromServer(r.play);
          else showToast('Не удалось','err');
        }catch(e){ showToast('Ошибка','err'); }
      };
      body.appendChild(btn);
    });
    if(!body.children.length) body.innerHTML = '<div class="empty">Пусто</div>';
  }catch(e){
    body.innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
}

// ==================== CHART / RELEASES / STATIONS ====================
async function loadChart(){
  const list = $('chart-list');
  list.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try{
    const data = await api('/api/chart');
    const tracks = data || [];
    list.innerHTML = '';
    tracks.forEach((t, i)=>{
      const row = makeTrackRow(t, i, ({row}) => {
        const isCurrent = state.track && String(state.track.id) === String(t.id);
        if(isCurrent){ pauseWithAnim(row); return; }
        api('/api/play_track',{method:'POST',body:JSON.stringify({id:t.id})})
          .then(r=>{ if(r.play) playFromServer(r.play); });
      });
      list.appendChild(row);
    });
  }catch(e){
    list.innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
}
async function loadReleases(){
  const grid = $('releases-grid');
  grid.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try{
    const data = await api('/api/new_releases');
    grid.innerHTML = '';
    (data||[]).forEach(al => {
      const card = document.createElement('div');
      card.className = 'album-card';
      const cov = al.cover_url ? `background-image:url('${proxyUrl(al.cover_url)}')` : '';
      const year = al.year ? `<div class="al-year">${al.year}${al.track_count ? ' · '+al.track_count+' треков' : ''}</div>` : '';
      card.innerHTML = `<div class="al-cover" style="${cov}"></div>
                        <div class="al-title">${esc(al.title)}</div>${year}`;
      card.onclick = () => openAlbum(al.id, al.title, al.cover_url);
      grid.appendChild(card);
    });
    if(!grid.children.length) grid.innerHTML = '<div class="empty">Пусто</div>';
  }catch(e){
    grid.innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
}
async function loadStations(){
  const grid = $('stations-grid');
  grid.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try{
    const data = await api('/api/stations');
    grid.innerHTML = '';
    (data||[]).forEach(st => {
      const card = document.createElement('div');
      card.className = 'station-card';
      const img = st.image ? `background-image:url('${proxyUrl(st.image)}')` : '';
      card.innerHTML = `<div class="st-img" style="${img}">📻</div>
                        <div class="st-name">${esc(st.name)}</div>`;
      card.onclick = () => {
        playWaveAnimation();
        send('my_wave', {seeds: [st.id]});
        switchView('library');
      };
      grid.appendChild(card);
    });
    if(!grid.children.length) grid.innerHTML = '<div class="empty">Станции недоступны</div>';
  }catch(e){
    grid.innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
}

// ==================== VIEWS ====================
function switchView(name){
  state.currentView = name;
  // FIX: сбрасываем скролл при переключении вида
  const contentEl = $('content');
  if(contentEl) contentEl.scrollTop = 0;

  document.querySelectorAll('.rail-btn').forEach(b=>b.classList.toggle('active', b.dataset.view===name));

  const VIEW_MAP = {
    library:  'np-view',
    liked:    'liked-view',
    playlist: 'pl-view',
    artist:   'artist-view',
    search:   'search-view',
    chart:    'chart-view',
    releases: 'releases-view',
    stations: 'stations-view',
  };

  Object.values(VIEW_MAP).forEach(id => {
    const el = $(id);
    if(el) el.style.display = 'none';
  });

  const targetId = VIEW_MAP[name];
  const target = targetId ? $(targetId) : null;
  if(target){
    target.style.display = '';
    target.style.animation = 'none';
    void target.offsetWidth;
    target.style.animation = '';
  }

  const m = {
    library:['Библиотека','Сейчас играет'],
    liked:['Библиотека','Любимые'],
    chart:['Обзор','Чарт'],
    releases:['Обзор','Новинки'],
    stations:['Обзор','Станции'],
    search:['Поиск','Найти трек'],
  };
  if(m[name]){ $('crumb-sec').textContent=m[name][0]; $('crumb-title').textContent=m[name][1]; }
  if(name==='playlist') $('crumb-sec').textContent='Плейлисты';
  if(name==='artist')   $('crumb-sec').textContent='Исполнитель';

  if(name==='chart' && !$('chart-list').children.length) loadChart();
  if(name==='releases' && !$('releases-grid').children.length) loadReleases();
  if(name==='stations' && !$('stations-grid').children.length) loadStations();
  if(name==='search')   setTimeout(()=>$('search-input').focus(),100);
}

// ==================== LIKED ====================
async function loadLiked(){
  if(state.likedLoading) return;
  state.likedLoading = true;
  const list = $('liked-list'); if(!list) return;
  list.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div>' +
    '<div style="margin-left:14px;color:#8d8da0;font-size:13px">Подгружаю треки…</div></div>';
  try{
    const data = await api('/api/liked');
    state.likedTracks = data.tracks || [];
    state.likedLoaded = true;
    renderLiked();
  }catch(e){
    list.innerHTML = '<div class="empty">Ошибка загрузки. ' +
      '<button class="btn-ghost" id="liked-retry" style="margin-left:8px">Повторить</button></div>';
    const rb = $('liked-retry');
    if(rb) rb.onclick = () => { state.likedLoaded=false; state.likedLoading=false; loadLiked(); };
    console.warn(e);
  }finally{
    state.likedLoading = false;
  }
}
function renderLiked(){
  const list = $('liked-list'); list.innerHTML='';
  $('liked-sub').textContent = `${state.likedTracks.length} треков`;
  if(!state.likedTracks.length){
    list.innerHTML = '<div class="empty">Пока пусто</div>';
    return;
  }
  const CHUNK = 200;
  let i = 0;
  function renderChunk(){
    const frag = document.createDocumentFragment();
    const end = Math.min(i + CHUNK, state.likedTracks.length);
    for(; i < end; i++){
      const t = state.likedTracks[i];
      const idx = i;
      const row = makeTrackRow(t, idx, ({row}) => {
        const isCurrent = state.track && String(state.track.id) === String(t.id);
        if(isCurrent){ pauseWithAnim(row); return; }
        row.classList.add('clicked');
        setTimeout(()=>row.classList.remove('clicked'), 600);
        api('/api/liked/play',{method:'POST',body:JSON.stringify({index:idx})})
          .then(r=>{ if(r.play) playFromServer(r.play); });
      });
      frag.appendChild(row);
    }
    list.appendChild(frag);
    if(i < state.likedTracks.length) requestAnimationFrame(renderChunk);
  }
  renderChunk();
}

// ==================== PAUSE ANIMATION ====================
function pauseWithAnim(row){
  if(!row) return;
  row.classList.remove('pause-anim');
  void row.offsetWidth;
  row.classList.add('pause-anim');
  setTimeout(()=>row.classList.remove('pause-anim'), 700);
  send('toggle');
}

// ==================== TRACK ROW ====================
function makeTrackRow(t, i, onClick){
  const row = document.createElement('div');
  row.className = 'track-row';
  row.dataset.trackId = t.id;
  const hasCover = !!t.cover_url;
  const covClass = hasCover ? 'm-cover' : 'm-cover empty';
  const covStyle = hasCover ? `background-image:url('${proxyUrl(t.cover_url)}')` : '';
  const artist = esc(t.artists || '—');
  const isCur = state.track && String(state.track.id) === String(t.id);
  row.innerHTML = `
    <span class="num">${i+1}</span>
    <div class="${covClass}" style="${covStyle}"></div>
    <div class="m-info">
      <div class="m-title">${esc(t.title)}</div>
      <div class="m-artist" data-artist-id="${esc(t.artist_id||'')}" data-artist="${esc(t.artists||'')}">${artist}</div>
    </div>
    <span class="m-dur">${fmtTime(t.duration)}</span>
    <button class="m-like${isCur && state.isLiked ? ' liked' : ''}" title="Мне нравится">
      <svg viewBox="0 0 24 24"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/></svg>
    </button>
    <button class="m-add" title="В плейлист">＋</button>
    <span class="m-play"><svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M7 5v14l12-7z"/></svg></span>`;
  row.onclick = (e) => {
    if(e.target.closest('.m-like') || e.target.closest('.m-add')) return;
    onClick({row, event: e});
  };
  const ar = row.querySelector('.m-artist');
  if(ar){
    ar.onclick = (e) => {
      e.stopPropagation();
      const aid = ar.dataset.artistId;
      const an = ar.dataset.artist;
      if(aid || an) openArtist(aid, an);
    };
  }
  const like = row.querySelector('.m-like');
  like.onclick = (e) => {
    e.stopPropagation();
    like.classList.toggle('liked');
    api('/api/like', {method:'POST', body: JSON.stringify({track_id: t.id, like: like.classList.contains('liked')})})
      .then(()=> {
        showToast(like.classList.contains('liked') ? '❤ В любимые' : 'Убрано из любимых');
        if(String(t.id) === String(state.track?.id)){
          state.isLiked = like.classList.contains('liked');
          renderLikeBtn();
        }
      })
      .catch(()=> showToast('Ошибка','err'));
  };
  const add = row.querySelector('.m-add');
  add.onclick = (e) => {
    e.stopPropagation();
    showAddMenu(e, t);
  };
  return row;
}

// ==================== ADD TO PLAYLIST MENU ====================
function showAddMenu(e, t){
  closeAddMenu();
  if(!state.playlists.length) loadPlaylists();
  const menu = document.createElement('div');
  menu.className = 'add-menu';
  menu.id = 'add-menu';
  state.playlists.slice(0, 12).forEach(pl => {
    const b = document.createElement('button');
    b.textContent = pl.title;
    b.onclick = async (ev) => {
      ev.stopPropagation();
      closeAddMenu();
      try{
        const r = await api('/api/playlists/add_track', {method:'POST',
          body: JSON.stringify({kind: pl.kind, track_id: t.id, album_id: t.album_id})});
        showToast(r.ok ? `Добавила в «${pl.title}»` : 'Не удалось', r.ok ? '' : 'err');
      }catch(err){ showToast('Ошибка','err'); }
    };
    menu.appendChild(b);
  });
  const nb = document.createElement('button');
  nb.className = 'new';
  nb.textContent = '＋ Новый плейлист';
  nb.onclick = async (ev) => {
    ev.stopPropagation();
    closeAddMenu();
    const title = prompt('Название плейлиста:');
    if(!title) return;
    try{
      const r = await api('/api/playlists/create', {method:'POST',
        body: JSON.stringify({title})});
      if(r.ok){
        state.playlists.push(r.playlist);
        await api('/api/playlists/add_track', {method:'POST',
          body: JSON.stringify({kind: r.playlist.kind, track_id: t.id, album_id: t.album_id})});
        showToast(`Создала «${title}» и добавила трек`);
      } else showToast('Не удалось создать','err');
    }catch(err){ showToast('Ошибка','err'); }
  };
  menu.appendChild(nb);
  document.body.appendChild(menu);
  const x = Math.min(e.clientX, window.innerWidth - 240);
  const y = Math.min(e.clientY, window.innerHeight - 300);
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  setTimeout(()=>document.addEventListener('click', closeAddMenu, {once:true}), 50);
}
function closeAddMenu(){
  const m = $('add-menu');
  if(m) m.remove();
}

// ==================== PLAYLIST VIEW ====================
async function openPlaylist(kind){
  const pl = state.playlists.find(p => String(p.kind)===String(kind));
  if(!pl) return;
  switchView('playlist');
  $('crumb-title').textContent = pl.title;
  $('pl-title').textContent = pl.title;
  $('pl-sub').textContent = `${pl.track_count} треков`;
  if(pl.cover_url){
    $('pl-cover').style.backgroundImage = `url("${proxyUrl(pl.cover_url)}")`;
    $('pl-cover').style.backgroundColor = 'var(--bg-rail)';
  } else {
    $('pl-cover').style.backgroundImage = 'linear-gradient(135deg,#a855f7,#ec4899)';
  }
  const list = $('pl-tracks');
  list.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try{
    const data = await api('/api/playlist_tracks?kind=' + encodeURIComponent(pl.kind));
    const tracks = data.tracks || [];
    list.innerHTML = '';
    if(!tracks.length){
      list.innerHTML = '<div class="empty">Плейлист пуст</div>';
    } else {
      tracks.forEach((t, i)=>{
        const row = makeTrackRow(t, i, ({row}) => {
          const isCurrent = state.track && String(state.track.id) === String(t.id);
          if(isCurrent){ pauseWithAnim(row); return; }
          row.classList.add('clicked');
          setTimeout(()=>row.classList.remove('clicked'), 600);
          send('play_playlist_track',{kind:pl.kind,index:i});
        });
        list.appendChild(row);
      });
    }
  }catch(e){
    list.innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
  $('pl-play').onclick = () => send('play_playlist_track',{kind:pl.kind,index:0});
}

// ==================== ARTIST VIEW ====================
async function openArtist(artistId, artistName){
  if(!artistId && !artistName) return;
  const key = (artistId || artistName).toString().toLowerCase();
  switchView('artist');
  $('crumb-title').textContent = artistName || 'Исполнитель';
  $('artist-name').textContent = artistName || '—';
  $('artist-counts').innerHTML = '';
  $('artist-desc').textContent = '';
  $('artist-banner').style.backgroundImage = '';
  $('artist-cover').style.backgroundImage = '';
  $('artist-albums').innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  $('artist-top').innerHTML = '';
  $('artist-videos').innerHTML = '';
  $('artist-videos-sec').style.display = 'none';
  $('artist-top-sec').style.display = 'none';
  $('artist-albums-sec').style.display = 'block';

  try{
    let info = state.artistCache[key];
    if(!info){
      const q = artistId ? 'id=' + encodeURIComponent(artistId) : 'name=' + encodeURIComponent(artistName);
      const r = await api('/api/artist?' + q);
      info = r.artist;
      if(info) state.artistCache[key] = info;
    }
    if(!info){
      $('artist-name').textContent = 'Не найдено';
      $('artist-albums').innerHTML = '<div class="empty">Исполнитель не найден</div>';
      return;
    }
    renderArtist(info);
  }catch(e){
    console.warn(e);
    $('artist-albums').innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
}
function renderArtist(a){
  $('crumb-title').textContent = a.name;
  $('artist-name').textContent = a.name;
  if(a.banner_url) $('artist-banner').style.backgroundImage = `url("${proxyUrl(a.banner_url)}")`;
  if(a.cover_url)  $('artist-cover').style.backgroundImage = `url("${proxyUrl(a.cover_url)}")`;
  const c = a.counts || {};
  const parts = [];
  if(c.likes) parts.push(`<span><b>${fmtNum(c.likes)}</b> слушателей</span>`);
  if(c.tracks) parts.push(`<span><b>${c.tracks}</b> треков</span>`);
  if(c.albums) parts.push(`<span><b>${c.albums}</b> альбомов</span>`);
  $('artist-counts').innerHTML = parts.join('');
  $('artist-desc').textContent = a.description || '';

  const top = a.top_tracks || [];
  if(top.length){
    $('artist-top-sec').style.display = 'block';
    const box = $('artist-top'); box.innerHTML = '';
    top.forEach((t, i) => {
      const row = makeTrackRow(t, i, ({row}) => {
        const isCurrent = state.track && String(state.track.id) === String(t.id);
        if(isCurrent){ pauseWithAnim(row); return; }
        row.classList.add('clicked');
        setTimeout(()=>row.classList.remove('clicked'), 600);
        api('/api/play_track',{method:'POST',body:JSON.stringify({id:t.id})})
          .then(r=>{ if(r.play) playFromServer(r.play); });
      });
      box.appendChild(row);
    });
  } else {
    $('artist-top-sec').style.display = 'none';
  }

  const vids = a.videos || [];
  if(vids.length){
    $('artist-videos-sec').style.display = 'block';
    const vbox = $('artist-videos'); vbox.innerHTML = '';
    vids.forEach(v => {
      const card = document.createElement('div');
      card.className = 'video-card';
      const thumb = v.thumbnail ? `background-image:url('${proxyUrl(v.thumbnail)}')` : '';
      card.innerHTML = `<div class="vc-thumb" style="${thumb}"></div>
                        <div class="vc-title">${esc(v.title)}</div>`;
      if(v.url) card.onclick = () => window.open(v.url, '_blank');
      vbox.appendChild(card);
    });
  } else {
    $('artist-videos-sec').style.display = 'none';
  }

  const albums = a.albums || [];
  const box = $('artist-albums'); box.innerHTML = '';
  if(!albums.length){
    box.innerHTML = '<div class="empty">Нет альбомов</div>';
  } else {
    albums.forEach(al => {
      const card = document.createElement('div');
      card.className = 'album-card';
      const cov = al.cover_url
        ? `background-image:url('${proxyUrl(al.cover_url)}')`
        : '';
      const year = al.year
        ? `<div class="al-year">${al.year}${al.track_count ? ' · '+al.track_count+' треков' : ''}</div>`
        : '';
      card.innerHTML = `<div class="al-cover" style="${cov}"></div>
                        <div class="al-title">${esc(al.title)}</div>
                        ${year}`;
      card.onclick = () => openAlbum(al.id, al.title, al.cover_url);
      box.appendChild(card);
    });
  }

  $('artist-play').onclick = () => send('play_artist_top', {id: a.id});
  $('artist-wave').onclick = () => {
    playWaveAnimation();
    if(a.top_tracks && a.top_tracks[0]){
      send('my_wave_by_track', {track_id: a.top_tracks[0].id});
    }
  };
}

async function openAlbum(albumId, title, coverUrl){
  switchView('playlist');
  $('crumb-sec').textContent = 'Альбом';
  $('crumb-title').textContent = title;
  $('pl-title').textContent = title;
  $('pl-sub').textContent = 'Загрузка…';
  $('pl-cover').style.backgroundImage = coverUrl
    ? `url("${proxyUrl(coverUrl)}")`
    : 'linear-gradient(135deg,#a855f7,#ec4899)';
  const list = $('pl-tracks');
  list.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try{
    const data = await api('/api/album_tracks?id=' + encodeURIComponent(albumId));
    const tracks = data.tracks || [];
    list.innerHTML = '';
    $('pl-sub').textContent = `${tracks.length} треков`;
    if(!tracks.length){
      list.innerHTML = '<div class="empty">Пусто</div>';
    } else {
      tracks.forEach((t, i)=>{
        const row = makeTrackRow(t, i, ({row}) => {
          const isCurrent = state.track && String(state.track.id) === String(t.id);
          if(isCurrent){ pauseWithAnim(row); return; }
          row.classList.add('clicked');
          setTimeout(()=>row.classList.remove('clicked'), 600);
          send('play_album_track',{id:albumId,index:i});
        });
        list.appendChild(row);
      });
    }
  }catch(e){
    list.innerHTML = '<div class="empty">Ошибка загрузки</div>';
  }
  $('pl-play').onclick = () => send('play_album_track',{id:albumId,index:0});
}

// ==================== SEARCH ====================
let searchTimer = null;
let suggestTimer = null;
$('search-input').addEventListener('input', e=>{
  clearTimeout(searchTimer);
  clearTimeout(suggestTimer);
  const q = e.target.value.trim();
  if(!q){ $('search-results').innerHTML=''; $('search-suggest').innerHTML=''; return; }
  suggestTimer = setTimeout(async ()=>{
    try{
      const r = await api('/api/suggest?q='+encodeURIComponent(q));
      $('search-suggest').innerHTML = (r.suggestions||[])
        .map(s=>`<option value="${esc(s)}">`).join('');
    }catch(err){}
  }, 250);
  searchTimer = setTimeout(()=>doSearch(q), 350);
});
$('search-input').addEventListener('keydown', e=>{
  if(e.key==='Enter'){ const q=e.target.value.trim(); if(q) doSearch(q); }
});
async function doSearch(q){
  const list = $('search-results');
  list.innerHTML = '<div class="spinner-wrap"><div class="spinner"></div></div>';
  try{
    const data = await api('/api/search?q='+encodeURIComponent(q));
    const tracks = data.tracks||[];
    if(!tracks.length){ list.innerHTML='<div class="empty">Ничего не найдено</div>'; return; }
    list.innerHTML='';
    tracks.forEach((t,i)=>{
      const row = makeTrackRow(t, i, ({row}) => {
        const isCurrent = state.track && String(state.track.id) === String(t.id);
        if(isCurrent){ pauseWithAnim(row); return; }
        row.classList.add('clicked');
        setTimeout(()=>row.classList.remove('clicked'), 600);
        api('/api/play_track',{method:'POST',body:JSON.stringify({id:t.id})})
          .then(r=>{ if(r.play) playFromServer(r.play); });
      });
      list.appendChild(row);
    });
  }catch(e){ list.innerHTML='<div class="empty">Ошибка</div>'; }
}

// ==================== WAVE RADIAL MENU ====================
let waveMenuOpen = false;
function openWaveMenu(){
  const radial = $('wave-radial');
  const items = $('wave-radial-items');
  const center = $('wave-radial-center');

  const cx = window.innerWidth / 2;
  const cy = window.innerHeight * 0.45;
  center.style.left = cx + 'px';
  center.style.top  = cy + 'px';

  items.innerHTML = '';
  const R = Math.min(window.innerWidth, window.innerHeight) * 0.28;
  const total = WAVE_MOODS.length;
  WAVE_MOODS.forEach((m, i) => {
    const angle = (i / total) * 2 * Math.PI - Math.PI/2;
    const x = cx + Math.cos(angle) * R;
    const y = cy + Math.sin(angle) * R;
    const el = document.createElement('div');
    el.className = 'wave-radial-item';
    el.style.left = x + 'px';
    el.style.top  = y + 'px';
    el.style.transitionDelay = (i * 22) + 'ms';
    el.innerHTML = `${m.emoji}<div class="wr-label">${m.label}</div>`;
    el.onclick = (e) => {
      e.stopPropagation();
      closeWaveMenu();
      playWaveAnimation();
      send('my_wave', {seeds:[m.seed]});
      switchView('library');
    };
    items.appendChild(el);
  });

  radial.classList.add('open');
  waveMenuOpen = true;
}
function closeWaveMenu(){
  $('wave-radial').classList.remove('open');
  waveMenuOpen = false;
}
$('wave-radial-backdrop').onclick = closeWaveMenu;
document.addEventListener('keydown', e => {
  if(e.key === 'Escape'){
    if(waveMenuOpen) closeWaveMenu();
    if(state.queueOpen) closeQueue();
    document.querySelectorAll('.modal-back.open').forEach(m=>{
      if(m.id !== 'modal-back' && m.id !== 'acc-back') m.classList.remove('open');
    });
  }
});

// ==================== ACCOUNTS ====================
let accountsPopupOpen = false;

async function loadAccounts(){
  try{ const data = await api('/api/accounts'); renderAccounts(data); }catch(e){}
}
function renderAccounts(data){
  const btn = $('acc-btn'), popup = $('acc-popup');
  const active = (data.accounts||[]).find(a=>a.uid===data.active);
  const initial = active ? (active.display_name||active.login||'?')[0].toUpperCase() : '?';
  btn.innerHTML = active && active.avatar_url
    ? `<img src="${active.avatar_url}" onerror="this.style.display='none'">`
    : `<div style="width:100%;height:100%;display:grid;place-items:center;font-weight:700;color:#fff">${initial}</div>`;
  popup.innerHTML = '';
  (data.accounts||[]).forEach(a=>{
    const row = document.createElement('div');
    row.className = 'acc-item' + (a.uid===data.active?' active':'');
    const ini = (a.display_name||a.login||'?')[0].toUpperCase();
    const av = a.avatar_url
      ? `<img src="${a.avatar_url}" onerror="this.style.visibility='hidden'">`
      : `<div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#a855f7,#ec4899);display:grid;place-items:center;font-weight:700">${ini}</div>`;
    row.innerHTML = `${av}<div class="info"><div class="name">${esc(a.display_name)}</div><div class="login">@${esc(a.login)}</div></div><button class="rm" title="Удалить">✕</button>`;
    row.querySelector('.rm').onclick = e => {
      e.stopPropagation();
      api('/api/accounts/remove',{method:'POST',body:JSON.stringify({uid:a.uid})})
        .then(()=>{ loadAccounts(); showToast('Удалён'); });
    };
    row.onclick = () => {
      api('/api/accounts/switch',{method:'POST',body:JSON.stringify({uid:a.uid})})
        .then(()=>{
          loadAccounts();
          state.playlistsLoaded = false;
          state.likedLoaded = false;
          state.artistCache = {};
          loadPlaylists();
          loadForYou();
          showToast('Переключён');
        });
      closeAccountsPopup();
    };
    popup.appendChild(row);
  });
  const add = document.createElement('div');
  add.className = 'acc-add';
  add.innerHTML = '<span style="font-size:18px">+</span> Добавить аккаунт';
  add.onclick = () => { closeAccountsPopup(); showAuthModal(); };
  popup.appendChild(add);
}

function openAccountsPopup(){ accountsPopupOpen=true; $('acc-popup').classList.add('open'); }
function closeAccountsPopup(){ accountsPopupOpen=false; $('acc-popup').classList.remove('open'); }
function toggleAccountsPopup(){ accountsPopupOpen?closeAccountsPopup():openAccountsPopup(); }

$('acc-btn').addEventListener('pointerdown', (e) => {
  e.preventDefault(); e.stopPropagation();
  toggleAccountsPopup();
});
document.addEventListener('pointerdown', (e) => {
  if(!accountsPopupOpen) return;
  const btn = $('acc-btn'), popup = $('acc-popup');
  if(!btn.contains(e.target) && !popup.contains(e.target)) closeAccountsPopup();
});

// ==================== AUTH ====================
function showAuthModal(){ $('acc-back').classList.add('open'); }
$('acc-close').onclick = () => $('acc-back').classList.remove('open');
$('acc-open-oauth').onclick = () => {
  const url = "https://oauth.yandex.ru/authorize?response_type=token"
    + "&client_id=23cabbbdc6cd418abb4b39c32c41195d";
  window.open(url, '_blank');
  showToast('Скопируй токен из адреса');
};
$('acc-submit').onclick = async () => {
  const token = $('acc-token').value.trim();
  if(!token){ showToast('Вставь токен','err'); return; }
  const r = await api('/api/accounts/add',{method:'POST',body:JSON.stringify({token})});
  if(r.ok){
    showToast('Аккаунт добавлен');
    $('acc-back').classList.remove('open');
    $('acc-token').value = '';
    loadAccounts();
    state.playlistsLoaded = false;
    state.likedLoaded = false;
    state.artistCache = {};
    loadPlaylists();
    loadForYou();
  } else showToast(r.error||'Ошибка','err');
};

// ==================== ALWAYS LISTEN ====================
let listenStream = null, listenRecorder = null, listenActive = false;

async function startAlwaysListen(){
  if(listenActive) return;
  try{
    listenStream = await navigator.mediaDevices.getUserMedia({audio:true});
    listenRecorder = new MediaRecorder(listenStream);
    listenRecorder.ondataavailable = async (e) => {
      if(!e.data || e.data.size < 3000) return;
      if(state.isSpeaking) return;
      try{
        const wav = await blobToWav(e.data);
        await sendVoiceQuiet(wav);
      }catch(err){}
    };
    listenRecorder.start(3000);
    listenActive = true;
    $('btn-mic').title = 'Слушаю…';
  }catch(e){
    console.warn('always-listen:', e);
    showToast('Нет доступа к микрофону', 'err');
    state.alwaysListen = false;
    const cb = $('set-always-listen'); if(cb) cb.checked = false;
  }
}
function stopAlwaysListen(){
  if(listenRecorder){ try{ listenRecorder.stop(); }catch(e){} }
  if(listenStream){ listenStream.getTracks().forEach(t=>t.stop()); }
  listenActive = false;
  $('btn-mic').title = 'Голос';
}
async function sendVoiceQuiet(wav){
  try{
    const fd = new FormData();
    fd.append('audio', wav, 'voice.wav');
    const r = await fetch('/api/voice?quiet=1', {method:'POST', body:fd});
    const j = await r.json();
    if(j.text && j.reply){
      showToast(`Вы: ${j.text}`);
      showToast(j.reply, 'reply');
    }
  }catch(e){}
}

// ==================== MANUAL MIC ====================
let manualRecorder = null, manualChunks = [], manualRec = false, manualTimer = null;

async function toggleMic(){
  if(manualRec){ stopMic(); return; }
  try{
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    manualRecorder = new MediaRecorder(stream);
    manualChunks = [];
    manualRecorder.ondataavailable = e => { if(e.data.size) manualChunks.push(e.data); };
    manualRecorder.onstop = async () => {
      stream.getTracks().forEach(t=>t.stop());
      manualRec = false;
      $('btn-mic').classList.remove('rec');
      showToast('Распознаю…');
      const blob = new Blob(manualChunks,{type:'audio/webm'});
      const wav = await blobToWav(blob);
      try{
        const fd = new FormData();
        fd.append('audio', wav, 'voice.wav');
        const r = await fetch('/api/voice', {method:'POST', body:fd});
        const j = await r.json();
        if(j.text) showToast(`Вы: ${j.text}`);
        if(j.reply) showToast(j.reply, 'reply');
      }catch(e){}
    };
    manualRecorder.start();
    manualRec = true;
    $('btn-mic').classList.add('rec');
    clearTimeout(manualTimer);
    manualTimer = setTimeout(stopMic, 5000);
  }catch(e){ showToast('Нет доступа к микрофону','err'); }
}
function stopMic(){ clearTimeout(manualTimer); if(manualRecorder && manualRec) manualRecorder.stop(); }

// ==================== HELPERS ====================
function fmtTime(s){
  s = Math.max(0, Math.floor(s||0));
  return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
}
function fmtNum(n){
  n = Number(n)||0;
  if(n >= 1000000) return (n/1000000).toFixed(1)+'M';
  if(n >= 1000)    return (n/1000).toFixed(0)+'K';
  return String(n);
}
function esc(s){ return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showToast(text, kind=''){
  if(!text) return;
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = text;
  $('toasts').appendChild(el);
  setTimeout(()=>{ el.classList.add('out'); setTimeout(()=>el.remove(),300); }, 2500);
}
async function blobToWav(blob){
  if(!decodeCtx) decodeCtx = new (window.AudioContext||window.webkitAudioContext)();
  const arr = await blob.arrayBuffer();
  const ab = await decodeCtx.decodeAudioData(arr);
  const nCh = ab.numberOfChannels, sr = ab.sampleRate, len = ab.length;
  const buf = new ArrayBuffer(44+len*nCh*2);
  const v = new DataView(buf);
  const ws_ = (o,s)=>{for(let i=0;i<s.length;i++) v.setUint8(o+i,s.charCodeAt(i));};
  ws_(0,'RIFF'); v.setUint32(4,36+len*nCh*2,true); ws_(8,'WAVE');
  ws_(12,'fmt '); v.setUint32(16,16,true); v.setUint16(20,1,true);
  v.setUint16(22,nCh,true); v.setUint32(24,sr,true); v.setUint32(28,sr*nCh*2,true);
  v.setUint16(32,nCh*2,true); v.setUint16(34,16,true);
  ws_(36,'data'); v.setUint32(40,len*nCh*2,true);
  let off = 44;
  for(let i=0;i<len;i++) for(let c=0;c<nCh;c++){
    const s = Math.max(-1,Math.min(1, ab.getChannelData(c)[i]));
    v.setInt16(off, s<0?s*0x8000:s*0x7FFF, true); off+=2;
  }
  return new Blob([buf],{type:'audio/wav'});
}

// ==================== DEVICES ====================
async function loadDevices(){
  try{
    const d = await api('/api/devices');
    const micSel = $('set-mic');
    const outSel = $('set-output');
    if(!micSel || !outSel) return;
    micSel.innerHTML = '<option value="">По умолчанию</option>';
    (d.input||[]).forEach(dev => {
      const o = document.createElement('option');
      o.value = dev.index !== undefined ? dev.index : dev[0];
      o.textContent = `[${o.value}] ${dev.name || dev[1]}`;
      micSel.appendChild(o);
    });
    micSel.value = (d.current_input == null) ? '' : String(d.current_input);

    outSel.innerHTML = '<option value="">По умолчанию</option>';
    (d.output||[]).forEach(dev => {
      const o = document.createElement('option');
      o.value = dev.index !== undefined ? dev.index : dev[0];
      o.textContent = `[${o.value}] ${dev.name || dev[1]}`;
      outSel.appendChild(o);
    });
    outSel.value = (d.current_output == null) ? '' : String(d.current_output);
  }catch(e){ console.warn('devices:', e); }
}

// ==================== EVENTS ====================
$('btn-play').onclick = () => {
  if(!audioCtx) initAudioGraph();
  if(audioCtx && audioCtx.state==='suspended') audioCtx.resume();
  send('toggle');
};
$('btn-next').onclick = () => send('next');
$('btn-prev').onclick = () => send('previous');
$('btn-wave').onclick = () => openWaveMenu();
$('btn-eq').onclick = () => $('eq-panel').classList.toggle('open');
$('btn-mic').onclick = toggleMic;
$('btn-like').onclick = () => {
  if(state.track) toggleLike(state.track.id);
};
$('btn-lyrics').onclick = openLyrics;
$('btn-queue').onclick = openQueue;
$('queue-close').onclick = closeQueue;
$('queue-back').onclick = closeQueue;
$('lyrics-close').onclick = () => $('lyrics-back').classList.remove('open');
$('btn-shuffle').onclick = () => send('shuffle');
$('btn-repeat').onclick = () => send('repeat', {enabled: !state.repeat});
$('wb-more').onclick = () => send('wave_more');
$('wb-less').onclick = () => send('wave_less');
$('wb-settings').onclick = openWaveSettings;
$('wb-station').onclick = openWaveSettings;
$('wave-close').onclick = () => $('wave-back').classList.remove('open');
$('wv-save').onclick = saveWaveSettings;
$('btn-refresh').onclick = () => {
  state.playlistsLoaded = false;
  state.likedLoaded = false;
  state.artistCache = {};
  loadPlaylists();
  loadForYou();
  showToast('Обновляю…');
};

const ucPrev = $('uc-prev');
const ucNext = $('uc-next');
const ucPlay = $('uc-play');
if(ucPrev) ucPrev.onclick = () => send('previous');
if(ucNext) ucNext.onclick = () => send('next');
if(ucPlay) ucPlay.onclick = () => {
  if(!audioCtx) initAudioGraph();
  if(audioCtx && audioCtx.state==='suspended') audioCtx.resume();
  send('toggle');
};

$('np-artist').onclick = () => {
  if(state.track && state.track.artists){
    openArtist(state.track.artist_id, state.track.artists);
  }
};

$('vol').addEventListener('input', e => {
  const v = parseInt(e.target.value);
  setVolume(v, false);
  clearTimeout(state._volTimer);
  state._volTimer = setTimeout(()=>send('volume_set', {level:v}), 120);
});

const progressEl = $('progress');
progressEl.addEventListener('pointerdown', () => { state.isSeeking = true; });
progressEl.addEventListener('input', () => {
  const v = parseFloat(progressEl.value);
  progressEl.style.setProperty('--p', (v/10) + '%');
  const dur = state.duration || audio.duration || 0;
  if(dur > 0){ $('cur-t').textContent = fmtTime((v/1000)*dur); }
});
progressEl.addEventListener('change', () => {
  const dur = state.duration || audio.duration || 0;
  if(dur<=0){ state.isSeeking=false; return; }
  const target = (progressEl.value/1000)*dur;
  audio.currentTime = target;
  state.position = target;
  send('seek', {seconds: target});
  setTimeout(()=>state.isSeeking=false, 300);
});

document.querySelectorAll('.sec-head').forEach(h=>{
  h.onclick = () => { h.classList.toggle('closed'); const b=h.nextElementSibling; if(b) b.classList.toggle('closed'); };
});
document.querySelectorAll('.rail-btn[data-view]').forEach(b=>{
  b.onclick = () => {
    const v = b.dataset.view;
    if(v==='liked'){ if(!state.likedLoaded && !state.likedLoading) loadLiked(); switchView('liked'); }
    else switchView(v);
  };
});
document.querySelectorAll('.chan[data-view]').forEach(b=>{
  b.onclick = () => {
    const v = b.dataset.view;
    if(v==='liked'){ if(!state.likedLoaded && !state.likedLoading) loadLiked(); switchView('liked'); }
    else switchView(v);
  };
});

audio.addEventListener('timeupdate', ()=>{
  if(state.isSeeking) return;
  const t = audio.currentTime;
  state.position = t;
  const dur = state.duration || audio.duration || 0;
  updateTimelineUI(t, dur);
});
audio.addEventListener('loadedmetadata', ()=>{
  if(audio.duration && isFinite(audio.duration)){
    state.duration = audio.duration;
    $('dur-t').textContent = fmtTime(audio.duration);
  }
});

// FIX: НЕ шлём next отсюда — это делает серверный clock_loop.
// Двойной вызов next приводил к «самопереключению» треков.
audio.addEventListener('ended', ()=>{
  state.isPlaying = false;
  renderPlayBtn();
  const tid = state.track ? state.track.id : null;
  if(tid == null) return;
  const dur = audio.duration || state.duration || 0;
  const pos = audio.currentTime || 0;
  // Защита от «фантомного» ended при обрыве стрима:
  if(dur > 5 && pos < dur * 0.92){
    console.warn('[ended] premature', pos.toFixed(1), '/', dur.toFixed(1));
    return;
  }
  // Сервер сам переключит очередь
});

audio.addEventListener('play', ()=>{ state.isPlaying=true; renderPlayBtn(); const np=$('np'); if(np) np.classList.add('playing'); });
audio.addEventListener('pause', ()=>{ state.isPlaying=false; renderPlayBtn(); const np=$('np'); if(np) np.classList.remove('playing'); });

function renderEQPresets(){
  const c = $('eq-presets'); if(!c) return; c.innerHTML='';
  Object.keys(EQ_PRESETS).forEach(p => {
    const b = document.createElement('button');
    b.className = 'eq-preset' + (state.eqPreset===p && state.eqEnabled ? ' active' : '');
    b.textContent = p;
    b.onclick = () => {
      state.eqPreset=p; state.eqEnabled=true;
      applyEQ(true, p);
      send('set_eq', {preset:p, enabled:true});
      renderEQPresets();
      updateEqButton();
    };
    c.appendChild(b);
  });
  const t = $('eq-toggle'); if(t) t.checked = state.eqEnabled;
}
$('eq-toggle').addEventListener('change', e => {
  state.eqEnabled = e.target.checked;
  applyEQ(state.eqEnabled, state.eqPreset);
  send('set_eq', {preset: state.eqPreset, enabled: state.eqEnabled});
  renderEQPresets();
  updateEqButton();
});

$('btn-settings').onclick = () => {
  $('set-name').value = config_name();
  $('set-always-listen').checked = state.alwaysListen;
  $('modal-back').classList.add('open');
  loadDevices();
};
function config_name(){
  return document.querySelector('#assistant-title')?.textContent || 'Elena';
}
$('modal-close').onclick = () => $('modal-back').classList.remove('open');
$('set-name').addEventListener('change', e => send('change_name', {name: e.target.value.trim()}));
$('set-quality').addEventListener('change', e => send('set_quality', {quality: e.target.value}));
$('set-speaker').addEventListener('change', e => send('set_speaker', {speaker: e.target.value}));
$('set-always-listen').addEventListener('change', e => {
  state.alwaysListen = e.target.checked;
  try{ localStorage.setItem('elena_always_listen', state.alwaysListen?'1':'0'); }catch(err){}
  if(state.alwaysListen) startAlwaysListen();
  else stopAlwaysListen();
});
$('set-mic').addEventListener('change', async e => {
  const idx = e.target.value === '' ? null : parseInt(e.target.value);
  const r = await api('/api/set_device', {
    method:'POST', body: JSON.stringify({kind:'input', index: idx})
  });
  if(r.ok) showToast(r.message || 'Микрофон изменён');
  else showToast(r.error || 'Ошибка', 'err');
});
$('set-output').addEventListener('change', async e => {
  const idx = e.target.value === '' ? null : parseInt(e.target.value);
  const r = await api('/api/set_device', {
    method:'POST', body: JSON.stringify({kind:'output', index: idx})
  });
  if(r.ok) showToast(r.message || 'Устройство вывода изменено');
  else showToast(r.error || 'Ошибка', 'err');
});

document.addEventListener('keydown', e => {
  const inInput = document.activeElement && document.activeElement.matches('input,textarea');
  switch(e.key){
    case 'ArrowRight': if(!inInput){ send('seek_relative',{delta:10}); e.preventDefault(); } break;
    case 'ArrowLeft':  if(!inInput){ send('seek_relative',{delta:-10}); e.preventDefault(); } break;
    case 'ArrowUp':    if(!inInput){ send('volume_up'); e.preventDefault(); } break;
    case 'ArrowDown':  if(!inInput){ send('volume_down'); e.preventDefault(); } break;
    case 'Enter':      if(!inInput){ send('toggle'); e.preventDefault(); } break;
    case ' ':          if(!inInput){ send('toggle'); e.preventDefault(); } break;
    case 'MediaTrackNext': send('next'); break;
    case 'MediaTrackPrevious': send('previous'); break;
  }
});

// ==================== EQ BUTTON STATE ====================
function updateEqButton(){
  const b = $('btn-eq');
  if(b) b.classList.toggle('eq-on', !!state.eqEnabled);
}

// ==================== INIT ====================
async function init(){
  initAudioGraph();
  audio.volume = state.volume/100;
  updateVolUI(state.volume);
  renderPlayBtn();
  renderEQPresets();
  updateEqButton();
  resizeCanvas();

  let saved = null;
  try{ saved = localStorage.getItem('elena_always_listen'); }catch(e){}
  if(saved === null){
    state.alwaysListen = true;
    try{ localStorage.setItem('elena_always_listen', '1'); }catch(e){}
  } else {
    state.alwaysListen = saved === '1';
  }

  await pollState();
  loadPlaylists();
  loadForYou();
  loadAccounts();
  setInterval(pollState, POLL_MS);
  setInterval(loadAccounts, 30000);

  if(state.alwaysListen){
    setTimeout(() => startAlwaysListen(), 800);
  }
}
window.addEventListener('DOMContentLoaded', init);
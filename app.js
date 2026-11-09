// Gym planner — app logic. Data lives in Supabase (see db/schema.sql); the plan is loaded from the `plans` table.
/* global supabase, GYM_CONFIG */
const IMG_BASE = 'https://cdn.jsdelivr.net/gh/yuhonas/free-exercise-db/exercises/';
function imgSrc(path){ return path ? IMG_BASE + path : ''; }
const db = supabase.createClient(GYM_CONFIG.url, GYM_CONFIG.key, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } });

let PLAN = {}, PLAN_META = {}, PLAN_ID = null;
let STAGE_LABEL = { warmup:'Warm-up', main:'Main session', cooldown:'Cool-down', mobility:'Mobility' };
let SESSION_LABEL = { morning:'Morning', lower:'Lower', upperH:'Upper H', upperV:'Upper V', full:'Home / Stability' };
const SPORTS = [['run','Run'],['cycle','Cycle'],['swim','Swim'],['football','Football'],['climb','Climb'],['tennis','Tennis'],
                ['snowboard','Snowboard'],['surf','Surf'],['walk','Walk'],['mobility','Mobility'],['gym','Gym (other)'],['other','Other']];

// drafts[day] = the in-progress session for that gym day, saved to Supabase as an activity with status 'in_progress'
// { id, started_at, checked:{exId:true}, sets:{exId:[{w,r}]}, notes:{exId}, swaps:{exId:n} }
let drafts = {};
let lastSets = {};        // exercise name -> {day, sets:[{weight_kg,reps,seconds}]}
let session='lower', idx=0, dir=1, pos=0, loaded=false, view='workout';
let videoView = false;
let userEmail = '';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const todayISO = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,10); };
function toast(msg, bad){ const t=document.getElementById('toast'); t.textContent=msg; t.className='toast show'+(bad?' bad':''); clearTimeout(toast.id); toast.id=setTimeout(()=>t.className='toast',2600); }

// ---------------------------------------------------------------- sign in
async function start(){
  const { data:{ session:s } } = await db.auth.getSession();
  if(!s){ return showLogin(); }
  await loadAll();
}
db.auth.onAuthStateChange((ev) => { if(ev === 'SIGNED_IN' && !loaded) loadAll(); });

function showLogin(sentTo){
  setTabs(null);
  document.getElementById('root').innerHTML = `
    <div class="slide login">
      <h2 class="ex-name">Sign in</h2>
      ${sentTo ? `<p class="ex-detail">Check <b>${esc(sentTo)}</b> and tap <b>Sign in</b> in the email. Whichever browser that opens is the one that gets signed in.</p>
        <a class="videolink" onclick="showLogin()">Back</a>`
      : `<form class="form" onsubmit="signInPassword(); return false;">
          <label>Email<input id="email" type="email" autocomplete="username" required></label>
          <label>Password<input id="password" type="password" autocomplete="current-password"></label>
          <div class="finish"><button type="submit">Sign in</button></div>
        </form>
        <a class="extra-link" onclick="sendLink()">No password yet? Email me a sign-in link</a>`}
    </div>`;
}
async function signInPassword(){
  const email = document.getElementById('email').value.trim(), password = document.getElementById('password').value;
  if(!password) return toast('Enter your password, or use the email link below', true);
  const { error } = await db.auth.signInWithPassword({ email, password });
  if(error) return toast(error.message, true);
  await loadAll();
}
async function sendLink(){
  const email = document.getElementById('email').value.trim();
  if(!email) return toast('Enter your email first', true);
  const { error } = await db.auth.signInWithOtp({ email, options:{ shouldCreateUser:false, emailRedirectTo: location.origin + location.pathname } });
  if(error) return toast(error.message, true);
  showLogin(email);
}
function showPassword(){
  view='password'; setTabs('history');
  document.getElementById('root').innerHTML = back() + `
    <div class="slide">
      <h2 class="ex-name">Set a password</h2>
      <p class="ex-detail">Then you can sign in on any browser straight away, without waiting for an email.</p>
      <form class="form" onsubmit="savePassword(); return false;">
        <input type="email" autocomplete="username" value="${esc(userEmail)}" style="display:none" readonly>
        <label>New password<input id="new-password" type="password" autocomplete="new-password" minlength="8" required></label>
        <div class="finish"><button type="submit">Save password</button></div>
      </form>
    </div>`;
}
async function savePassword(){
  const password = document.getElementById('new-password').value;
  if(password.length < 8) return toast('Use at least 8 characters', true);
  const { error } = await db.auth.updateUser({ password });
  if(error) return toast(error.message, true);
  toast('Password saved'); showHistory();
}
async function signOut(){ await db.auth.signOut(); loaded=false; showLogin(); }

// ---------------------------------------------------------------- load + save
async function loadAll(){
  const { data:{ user } } = await db.auth.getUser(); userEmail = user?.email || '';
  const [{ data:plans, error:e1 }, { data:open, error:e2 }, { data:sets, error:e3 }] = await Promise.all([
    db.from('plans').select('id,version,plan').eq('active', true).limit(1),
    db.from('activities').select('id,session_key,started_at,data').eq('type','gym').eq('status','in_progress'),
    db.from('sets').select('exercise,weight_kg,reps,seconds,set_no,done,created_at,activities(day,status)').order('created_at',{ascending:false}).limit(400),
  ]);
  if(e1||e2||e3){ document.getElementById('root').innerHTML = `<div class="empty">Couldn't load your data.<br>${esc((e1||e2||e3).message)}</div>`; return; }
  if(!plans.length){ document.getElementById('root').innerHTML = '<div class="empty">No active plan yet.</div>'; return; }
  PLAN_ID = plans[0].id; PLAN_META = plans[0].plan; PLAN = PLAN_META.days;
  if(PLAN_META.stage_labels) STAGE_LABEL = PLAN_META.stage_labels;
  if(PLAN_META.day_labels) SESSION_LABEL = PLAN_META.day_labels;
  buildTabs();
  drafts = {};
  for(const a of open){ drafts[a.session_key] = Object.assign(emptyDraft(), a.data||{}, { id:a.id, started_at:a.started_at }); }
  for(const day of Object.keys(PLAN)){
    const local = readLocal(day);                       // unsaved edits from a dropped connection
    if(local && (!drafts[day] || (local.saved_at||0) > (drafts[day].saved_at||0))) drafts[day] = Object.assign(emptyDraft(), drafts[day]||{}, local);
  }
  lastSets = {};
  for(const s of sets){
    if(!s.activities || s.activities.status !== 'done') continue;
    const k = s.exercise, d = s.activities.day;
    if(!lastSets[k]) lastSets[k] = { day:d, sets:[] };
    if(lastSets[k].day === d) lastSets[k].sets.push(s);
  }
  for(const k in lastSets) lastSets[k].sets.sort((a,b)=>a.set_no-b.set_no);
  if(!PLAN[session]) session = PLAN_META.day_order?.[0] || Object.keys(PLAN)[0];
  loaded = true; view='workout'; renderSlide();
}
function emptyDraft(){ return { checked:{}, sets:{}, notes:{}, swaps:{} }; }
function draft(){ return drafts[session] || (drafts[session] = emptyDraft()); }
function readLocal(day){ try { return JSON.parse(localStorage.getItem('gym-draft-'+day) || 'null'); } catch(e){ return null; } }
function writeLocal(day, d){ try { localStorage.setItem('gym-draft-'+day, JSON.stringify(d)); } catch(e){} }
function clearLocal(day){ try { localStorage.removeItem('gym-draft-'+day); } catch(e){} }

let saveTimer = null;
function saveState(){
  const day = session, d = draft();
  if(!d.started_at) d.started_at = new Date().toISOString();
  d.saved_at = Date.now();
  writeLocal(day, d);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => pushDraft(day), 700);
}
async function pushDraft(day){
  const d = drafts[day]; if(!d) return;
  const { id, ...data } = d;
  let res;
  if(id){ res = await db.from('activities').update({ data }).eq('id', id); }
  else {
    res = await db.from('activities').insert({ day: todayISO(), type:'gym', session_key:day, title:SESSION_LABEL[day]+' day', status:'in_progress',
                                              started_at:d.started_at, plan_id:PLAN_ID, source:'app', data }).select('id').single();
    if(!res.error) d.id = res.data.id;
  }
  if(res.error) toast('Not saved yet — will retry when you\'re back online', true);
  else writeLocal(day, d);
}
window.addEventListener('online', () => { for(const day in drafts) if(drafts[day].saved_at) pushDraft(day); });

// ---------------------------------------------------------------- the workout cards
function exId(){ return PLAN[session][idx].id; }
function toggleVideoView(){ videoView=!videoView; renderSlide(); }
function activeEx(){
  const base = PLAN[session][idx];
  const v = draft().swaps[base.id];
  if(!v || !base.alts || !base.alts[v-1]) return base;
  const a = base.alts[v-1];
  return { id:base.id, stage:base.stage, detail:base.detail, log:base.log, prog:(a.prog||base.prog),
           name:a.name, target:a.target, startW:a.startW, imgs:a.imgs, svgs:a.svgs, caps:a.caps, cues:a.cues, vid:a.vid, yt:a.yt, alts:base.alts };
}
function cycleSwap(){
  const base = PLAN[session][idx];
  if(!base.alts || !base.alts.length) return;
  const cur = draft().swaps[base.id]||0;
  draft().swaps[base.id] = (cur+1) % (base.alts.length+1);
  delete draft().sets[base.id];                  // a different exercise: start its sets fresh
  saveState(); pos=0; renderSlide();
}
function resetSwap(){ draft().swaps[exId()]=0; delete draft().sets[exId()]; saveState(); pos=0; renderSlide(); }

function setSession(s){ session=s; idx=0; dir=1; pos=0; view='workout'; renderSlide(); }
function go(d){ const list=PLAN[session]; const n=idx+d; if(n<0||n>=list.length) return; dir=d; idx=n; pos=0; renderSlide(); }
function toggleDone(){ const d=draft(); d.checked[exId()]=!d.checked[exId()]; saveState(); renderSlide(); }
function setN(v){ draft().notes[exId()]=v; saveState(); }

function setPlan(detail){
  const m = detail.match(/(\d+)\s*x\s*(\d+)(?:\s*-\s*(\d+))?/i);
  return m ? { n:parseInt(m[1]), lo:parseInt(m[2]), hi:parseInt(m[3]||m[2]) } : { n:3, lo:null, hi:null };
}
function setsFor(ex){
  const d = draft(); const sp = setPlan(ex.detail);
  if(!d.sets[ex.id]) d.sets[ex.id] = Array.from({length: sp.n}, () => ({ w:'', r:'' }));
  return d.sets[ex.id];
}
function setVal(i, field, v){ const ex=activeEx(); setsFor(ex)[i][field]=v; saveState(); }
// ---- extra exercises (not on the cards)
function addExtra(){ const d=draft(); (d.extra = d.extra || []).push({ name:'', sets:[{w:'',r:''},{w:'',r:''},{w:'',r:''}], note:'' }); saveState(); goExtras(); }
function goExtras(){ dir=1; idx=PLAN[session].length-1; renderSlide(); setTimeout(()=>{ const el=document.getElementById('extras'); if(el) el.scrollIntoView({behavior:'smooth'}); }, 50); }
function setExtra(i, field, v){ draft().extra[i][field]=v; saveState(); }
function setExtraSet(i, k, field, v){ draft().extra[i].sets[k][field]=v; saveState(); }
function addExtraSet(i){ const s=draft().extra[i].sets; s.push({ w:(s[s.length-1]||{}).w||'', r:'' }); saveState(); renderSlide(); }
function removeExtra(i){ draft().extra.splice(i,1); saveState(); renderSlide(); }
function extrasBlock(){
  const ex = draft().extra || [];
  const items = ex.map((x,i)=>`<div class="extra">
      <div class="log-row"><input placeholder="Exercise name" value="${esc(x.name)}" oninput="setExtra(${i},'name',this.value)"><button class="small-btn ghost" onclick="removeExtra(${i})" aria-label="Remove">✕</button></div>
      ${x.sets.map((s,k)=>`<div class="set-row"><span class="set-no">${k+1}</span>
        <input inputmode="decimal" placeholder="kg" value="${esc(s.w)}" oninput="setExtraSet(${i},${k},'w',this.value)"><span class="x">×</span>
        <input inputmode="numeric" placeholder="reps" value="${esc(s.r)}" oninput="setExtraSet(${i},${k},'r',this.value)"></div>`).join('')}
      <div class="set-tools"><button onclick="addExtraSet(${i})">+ set</button></div>
      <div class="log-row"><input placeholder="How it felt" value="${esc(x.note)}" oninput="setExtra(${i},'note',this.value)"></div>
    </div>`).join('');
  return `<div class="extras" id="extras"><div class="timer-head">Not on the cards</div>${items}
    <div class="set-tools"><button onclick="addExtra()">+ Add an exercise I did</button></div></div>`;
}
function addSet(){ const ex=activeEx(); const s=setsFor(ex); const last=s[s.length-1]||{w:'',r:''}; s.push({ w:last.w, r:'' }); saveState(); renderSlide(); }
function removeSet(){ const ex=activeEx(); const s=setsFor(ex); if(s.length>1){ s.pop(); saveState(); renderSlide(); } }
function applyWeight(w){ const ex=activeEx(); setsFor(ex).forEach(s=>{ if(!s.w) s.w=w; }); saveState(); renderSlide(); }

const num = v => { const m = String(v||'').replace(',', '.').match(/-?\d+(\.\d+)?/); return m ? parseFloat(m[0]) : null; };
function fmtKg(x){ return x==null ? '' : (Math.round(x*10)/10)+'kg'; }

function suggestion(ex){
  const last = lastSets[ex.name];
  if(!last || !last.sets.length) return null;
  const sp = setPlan(ex.detail), hold = parseHold(ex.detail) !== null;
  const ws = last.sets.map(s=>s.weight_kg).filter(x=>x!=null);
  const w = ws.length ? Math.max(...ws) : null;
  const what = last.sets.map(s => hold ? (s.seconds!=null ? s.seconds+'s' : '–') : (s.reps ?? '–')).join(', ');
  const dayTxt = new Date(last.day+'T12:00').toLocaleDateString(undefined,{day:'numeric',month:'short'});
  let next = null;
  if(!hold && w!=null && sp.hi && last.sets.length >= sp.n && last.sets.every(s => (s.reps||0) >= sp.hi)){
    next = w + 2;     // double progression: every set at the top of the range -> move up (2kg is the smallest dumbbell step)
  }
  return { text:`Last time (${dayTxt}): ${w!=null?fmtKg(w)+' × ':''}${what}`, w, next };
}

async function finishSession(){
  const d = draft(), list = PLAN[session];
  if(!confirm(`Finish and save your ${SESSION_LABEL[session]} day?`)) return;
  clearTimeout(saveTimer);
  if(!d.id) await pushDraft(session);
  if(!d.id) return toast('Couldn\'t save — check your connection and try again', true);
  const rows = [];
  list.forEach((base, i) => {
    idx = i; const ex = activeEx();
    const sets = d.sets[base.id] || [];
    const hold = parseHold(ex.detail) !== null;
    const before = rows.length;
    sets.forEach((s, k) => {
      const w = num(s.w), r = num(s.r);
      if(w==null && r==null) return;
      rows.push({ activity_id:d.id, exercise:ex.name, slot:base.id, set_no:k+1, weight_kg:w,
                  reps: hold ? null : (r==null?null:Math.round(r)), seconds: hold ? (r==null?null:Math.round(r)) : null,
                  done: !!d.checked[base.id], note: k===0 ? (d.notes[base.id]||null) : null });
    });
    // ticked with no numbers (warm-ups, mobility, or a lift not written down): still a record of what I did
    if(rows.length === before && d.checked[base.id]){
      rows.push({ activity_id:d.id, exercise:ex.name, slot:base.id, set_no:1, done:true, note: d.notes[base.id]||null });
    }
  });
  // exercises I did that aren't on the cards
  (d.extra||[]).forEach(x => {
    const name = (x.name||'').trim(); if(!name) return;
    const before = rows.length;
    (x.sets||[]).forEach((s, k) => {
      const w = num(s.w), r = num(s.r);
      if(w==null && r==null) return;
      rows.push({ activity_id:d.id, exercise:name, slot:null, set_no:k+1, weight_kg:w, reps: r==null?null:Math.round(r), done:true, note: k===0 ? (x.note||null) : null });
    });
    if(rows.length === before) rows.push({ activity_id:d.id, exercise:name, slot:null, set_no:1, done:true, note: x.note||null });
  });
  idx = list.length - 1;
  if(rows.length){ const { error } = await db.from('sets').insert(rows); if(error) return toast(error.message, true); }
  const mins = d.started_at ? Math.round((Date.now() - new Date(d.started_at)) / 60000) : null;
  const { id, ...data } = d;
  const { error } = await db.from('activities').update({ status:'done', duration_min: (mins && mins < 240) ? mins : null, data }).eq('id', d.id);
  if(error) return toast(error.message, true);
  clearLocal(session); delete drafts[session];
  toast('Saved'); await loadAll(); showHistory();
}

function videoUrl(term){ return 'https://www.youtube.com/results?search_query='+encodeURIComponent(/form/i.test(term) ? term : term+' form'); }
function openVideo(e){ return true; }

// ---- Timer feature (unchanged from the original app) ----
let timer = { running:false, remaining:0, total:0, id:null, mode:'hold', label:'' };
function parseHold(detail){
  const d = detail.toLowerCase();
  let m = d.match(/(\d+)\s*min/);
  if(m) return parseInt(m[1])*60;
  let s = d.match(/(\d+)\s*-\s*(\d+)\s*sec/);
  if(s) return parseInt(s[2]);
  s = d.match(/(\d+)\s*sec/);
  if(s) return parseInt(s[1]);
  return null;
}
function timerInfo(ex){
  const hold = parseHold(ex.detail);
  if(hold!==null){
    const perSide = /each side|each leg/i.test(ex.detail);
    return { mode:'hold', seconds:hold, label: perSide ? 'Hold each side' : 'Hold' };
  }
  const sets = ex.detail.match(/\d+\s*x\s*(\d+)(?:\s*-\s*(\d+))?/i);
  if(sets){
    const topReps = parseInt(sets[2]||sets[1]);
    return { mode:'rest', seconds: topReps<=8 ? 90 : 60, label:'Rest between sets' };
  }
  return { mode:'none', seconds:0, label:'' };
}
function fmtTime(s){ const m=Math.floor(s/60), ss=s%60; return m>0 ? (m+':'+String(ss).padStart(2,'0')) : (ss+'s'); }
function stopTick(){ if(timer.id){ clearInterval(timer.id); timer.id=null; } }
function resetTimer(secs, mode, label){ stopTick(); timer.running=false; timer.total=secs; timer.remaining=secs; timer.mode=mode; timer.label=label; updateTimerUI(); }
function toggleTimer(){
  if(timer.running){ stopTick(); timer.running=false; updateTimerUI(); return; }
  if(timer.remaining<=0) timer.remaining=timer.total;
  timer.running=true;
  timer.id=setInterval(()=>{
    timer.remaining--;
    if(timer.remaining<=0){ timer.remaining=0; stopTick(); timer.running=false; updateTimerUI(true); if(navigator.vibrate) navigator.vibrate([200,100,200]); }
    else { updateTimerUI(); }
  },1000);
  updateTimerUI();
}
function setRest(secs){ resetTimer(secs,'rest','Rest between sets'); }
function updateTimerUI(done){
  const disp=document.getElementById('timer-display'), btn=document.getElementById('timer-btn'), ring=document.getElementById('timer-ring');
  if(!disp) return;
  disp.textContent = done ? 'Done!' : fmtTime(timer.remaining);
  disp.className = 'timer-display' + (done?' done':'') + (timer.running?' running':'');
  if(btn) btn.textContent = timer.running ? 'Pause' : (timer.remaining<timer.total && timer.remaining>0 ? 'Resume' : 'Start');
  if(ring){ const frac = timer.total? (timer.remaining/timer.total):0; ring.style.strokeDashoffset = 2*Math.PI*52*(1-frac); }
}

function setPos(p){
  pos=p;
  const ex=activeEx();
  const imgEl=document.getElementById('ex-img'), capEl=document.getElementById('pos-pill'), svgStage=document.getElementById('svg-stage');
  if(imgEl && ex.imgs){ imgEl.src=imgSrc(ex.imgs[p]); }
  if(svgStage && ex.svgs){ svgStage.innerHTML = ex.svgs[p] + `<span class="pos-pill" id="pos-pill">${ex.caps[p]||ex.caps[0]}</span>`; }
  if(capEl && !svgStage){ capEl.textContent=ex.caps[p]||ex.caps[0]; }
  document.querySelectorAll('.pos-toggle button').forEach((b,i)=>b.className = i===p?'active':'');
}

// ---------------------------------------------------------------- top bar
function buildTabs(){
  const order = PLAN_META.day_order || Object.keys(PLAN);
  const short = { full:'Home' };
  document.getElementById('tabs').innerHTML = order.filter(d=>PLAN[d]).map(d =>
    `<button id="sw-${d}" onclick="setSession('${d}')">${esc(short[d]||SESSION_LABEL[d]||d)}</button>`).join('');
  document.getElementById('tools').style.display = '';
}
function setTabs(active){
  document.querySelectorAll('#tabs button').forEach(b => b.className = (b.id === 'sw-'+active) ? 'active' : '');
  document.getElementById('btn-history').className = 'icon-btn' + (active==='history'?' active':'');
  document.getElementById('btn-log').className = 'icon-btn' + (active==='log'?' active':'');
}

function renderSlide(){
  if(!loaded) return;
  view='workout'; setTabs(session);
  const list=PLAN[session], baseEx=list[idx], ex=activeEx(), d=draft();
  pos=0;
  const isDone=!!d.checked[baseEx.id];
  const enter = dir>=0?'24px':'-24px';

  let dots='<div class="dots">';
  list.forEach((e,i)=>{ let c='dot'; if(i===idx)c+=' active'; else if(d.checked[e.id])c+=' done'; dots+=`<span class="${c}"></span>`; });
  dots+='</div>';

  const playIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
  const aL='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>';
  const aR='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';

  let mediaStage;
  if(ex.svgs){ mediaStage = `<div class="photo-stage" id="svg-stage">${ex.svgs[0]}<span class="pos-pill" id="pos-pill">${ex.caps[0]}</span></div>`; }
  else if(ex.svg){ mediaStage = `<div class="photo-stage">${ex.svg}<span class="pos-pill" id="pos-pill">${ex.caps[0]}</span></div>`; }
  else if(ex.imgs){
    mediaStage = `<div class="photo-stage">
        <img id="ex-img" src="${imgSrc(ex.imgs[0])}" alt="${esc(ex.name)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
        <div class="photo-fallback">Image couldn't load — tap "Watch a real demo" below</div>
        <span class="pos-pill" id="pos-pill">${ex.caps[0]}</span>
      </div>`;
  } else { mediaStage = ''; }
  if(videoView){
    mediaStage = ex.yt
      ? `<div class="photo-stage"><iframe src="https://www.youtube-nocookie.com/embed/${ex.yt}?rel=0&playsinline=1" title="${esc(ex.name)} demo" referrerpolicy="strict-origin-when-cross-origin" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy"></iframe></div>`
      : `<div class="photo-stage"><div class="video-missing">No video picked for this one yet<a href="${videoUrl(ex.vid)}" target="_blank" rel="noopener">Search YouTube</a></div></div>`;
  }
  if(mediaStage){ mediaStage = `<div class="media-wrap"><button class="view-switch" onclick="toggleVideoView()">${videoView ? 'Picture' : '&#9654; Video'}</button>${mediaStage}</div>`; }

  let posToggle='';
  if(!videoView && ((ex.imgs && ex.imgs.length>1) || (ex.svgs && ex.svgs.length>1)) && ex.caps.length>1){
    posToggle = `<div class="pos-toggle"><button class="active" onclick="setPos(0)">${ex.caps[0]}</button><button onclick="setPos(1)">${ex.caps[1]}</button></div>`;
  }

  let cues='<ul class="cues">';
  ex.cues.forEach(c=>{ const cls = c.toLowerCase().includes('video')||c.toLowerCase().includes('no photo') ? ' class="watch"' : ''; cues+=`<li${cls}>${c}</li>`; });
  cues+='</ul>';

  const doneToggle = `<label class="done-toggle ${isDone?'checked':''}"><input type="checkbox" ${isDone?'checked':''} onchange="toggleDone()">${isDone?'Done':'Mark as done'}</label>`;
  let logBlock;
  if(ex.log){
    const sets = setsFor(ex), sp = setPlan(ex.detail), hold = parseHold(ex.detail) !== null;
    const n = esc(d.notes[baseEx.id]||'');
    const sug = suggestion(ex);
    const anyW = sets.some(s=>s.w);
    let hint='';
    if(sug){
      hint = `<div class="wt-hint suggest">${esc(sug.text)}</div>`;
      if(!anyW && sug.next!=null) hint += `<div class="wt-hint" onclick="applyWeight('${sug.next}')">Every set hit ${sp.hi} last time — try ${fmtKg(sug.next)} &middot; tap to use</div>`;
      else if(!anyW && sug.w!=null) hint += `<div class="wt-hint" onclick="applyWeight('${sug.w}')">Use ${fmtKg(sug.w)} again &middot; tap to fill</div>`;
    } else if(ex.startW && !anyW){
      hint=`<div class="wt-hint suggest">Suggested start: ${esc(ex.startW)}<span class="wt-note"> — adjust to how it feels</span></div>`;
    }
    const repPh = hold ? 'secs' : (sp.lo ? (sp.lo===sp.hi ? String(sp.lo) : sp.lo+'-'+sp.hi) : 'reps');
    const rows = sets.map((s,i)=>`<div class="set-row"><span class="set-no">${i+1}</span>
        <input inputmode="decimal" placeholder="kg" value="${esc(s.w)}" oninput="setVal(${i},'w',this.value)">
        <span class="x">×</span>
        <input inputmode="numeric" placeholder="${repPh}" value="${esc(s.r)}" oninput="setVal(${i},'r',this.value)"></div>`).join('');
    logBlock=`<div class="log-block">
      <div class="set-head"><span>Set</span><span>Weight</span><span></span><span>${hold?'Seconds':'Reps'}</span></div>
      ${rows}
      <div class="set-tools"><button onclick="addSet()">+ set</button>${sets.length>1?'<button onclick="removeSet()">− set</button>':''}</div>
      ${hint}
      <div class="log-row"><input type="text" placeholder="How it felt" value="${n}" oninput="setN(this.value)"></div>
      ${doneToggle}</div>`;
  } else {
    logBlock=`<div class="log-block">${doneToggle}</div>`;
  }

  const atStart=idx===0, atEnd=idx===list.length-1;
  const nav=`<div class="nav"><button class="prev" onclick="go(-1)" ${atStart?'disabled':''}>${aL} Prev</button><button class="next" onclick="go(1)" ${atEnd?'disabled':''}>Next ${aR}</button></div>`;
  const nExtra = (d.extra||[]).length;
  const finish = atEnd ? `${extrasBlock()}<div class="finish"><button onclick="finishSession()">Finish &amp; save ${SESSION_LABEL[session]} day</button></div>`
                       : `<a class="extra-link" onclick="goExtras()">+ Did something not on the cards?${nExtra ? ` (${nExtra} added)` : ''}</a>`;

  let swapControl='';
  if(baseEx.alts && baseEx.alts.length){
    const cur = d.swaps[baseEx.id]||0, total = baseEx.alts.length+1, isAlt = cur>0;
    const label = isAlt ? ('Swapped &middot; '+cur+' of '+(total-1)+' &mdash; tap to change') : 'Swap this exercise';
    const swapIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>';
    swapControl = `<button class="swap-btn ${isAlt?'swapped':''}" onclick="cycleSwap()">${swapIcon}<span>${label}</span></button>` +
      (isAlt ? `<button class="swap-reset" onclick="resetSwap()">Back to original</button>` : '');
  }

  const ti = timerInfo(ex), circ = 2*Math.PI*52;
  let timerBlock = '', restPresets = '';
  if(ti.mode!=='none'){
    if(ti.mode==='rest'){ restPresets = `<div class="rest-presets"><button onclick="setRest(30)">30s</button><button onclick="setRest(60)">60s</button><button onclick="setRest(90)">90s</button></div>`; }
    timerBlock = `<div class="timer-wrap">
      <div class="timer-head">${ti.mode==='hold'? 'Hold timer':'Rest timer'} &middot; <span class="timer-label">${ti.label}</span></div>
      <div class="timer-body">
        <div class="timer-ring-wrap">
          <svg viewBox="0 0 120 120" class="timer-svg"><circle cx="60" cy="60" r="52" class="ring-bg"/><circle cx="60" cy="60" r="52" class="ring-fg" id="timer-ring" style="stroke-dasharray:${circ};stroke-dashoffset:0"/></svg>
          <div class="timer-display" id="timer-display">${fmtTime(ti.seconds)}</div>
        </div>
        <div class="timer-controls">
          <button class="timer-btn" id="timer-btn" onclick="toggleTimer()">Start</button>
          <button class="timer-reset" onclick="resetTimer(${ti.seconds}, '${ti.mode}', '${ti.label.replace(/'/g,"")}')">Reset</button>
        </div>
      </div>
      ${restPresets}
    </div>`;
  }

  document.getElementById('root').innerHTML = `
    <div class="counter">${STAGE_LABEL[ex.stage]} &middot; ${idx+1} of ${list.length}</div>
    ${dots}
    <div class="slide" style="--enter:${enter}">
      <span class="stage-badge stage-${ex.stage}">${STAGE_LABEL[ex.stage]}</span>
      <h2 class="ex-name">${ex.name}</h2>
      <p class="ex-detail">${ex.detail}</p>
      ${ex.target ? `<div class="target-pill">${ex.target}</div>` : ''}
      ${swapControl}
      ${mediaStage}
      ${posToggle}
      ${cues}
      ${ex.prog ? `<div class="prog-note"><span class="prog-label">Progression</span>${ex.prog}</div>` : ''}
      <a class="videolink" href="${ex.yt ? 'https://www.youtube.com/watch?v='+ex.yt : videoUrl(ex.vid)}" target="_blank" rel="noopener">${playIcon} ${ex.yt ? 'Open on YouTube' : 'Watch a real demo'}</a>
      ${logBlock}
      ${timerBlock}
      ${nav}
      ${finish}
    </div>
    <p class="credit">Exercise photos from the public-domain free-exercise-db. Equipment may differ slightly from your plan — the movement is what matters.</p>`;
  const _ti = timerInfo(ex); stopTick(); timer.running=false; timer.total=_ti.seconds; timer.remaining=_ti.seconds; timer.mode=_ti.mode; timer.label=_ti.label; updateTimerUI();
}

// ---------------------------------------------------------------- history
function fmtDay(iso){ return new Date(iso+'T12:00').toLocaleDateString(undefined,{weekday:'short', day:'numeric', month:'short'}); }
const back = () => `<span class="backlink" onclick="setSession('${session}')"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg> Back to workouts</span>`;

async function showHistory(){
  view='history'; setTabs('history');
  document.getElementById('root').innerHTML = back() + '<div class="loading">Loading&hellip;</div>';
  const { data, error } = await db.from('activities')
    .select('id,day,type,session_key,title,duration_min,distance_km,effort,notes,source,sets(exercise,set_no,weight_kg,reps,seconds,done,note)')
    .eq('status','done').order('day',{ascending:false}).order('created_at',{ascending:false}).limit(40);
  if(error){ document.getElementById('root').innerHTML = back() + `<div class="empty">${esc(error.message)}</div>`; return; }
  let html = back();
  if(!data.length){ html += '<div class="empty">Nothing saved yet.<br>Finish a gym session, or log a run or other sport with <b>+</b>.</div>'; }
  html += '<div class="history-wrap">';
  for(const a of data){
    const tag = a.type==='gym' ? (SESSION_LABEL[a.session_key]||'Gym') : (SPORTS.find(s=>s[0]===a.type)||[0,a.type])[1];
    const bits = [a.duration_min?a.duration_min+' min':'', a.distance_km?a.distance_km+' km':'', a.effort?'effort '+a.effort+'/10':''].filter(Boolean).join(' · ');
    html += `<div class="history-entry"><div class="history-date-row"><span class="history-date">${fmtDay(a.day)}</span><span class="history-tag">${esc(tag)}</span></div>`;
    if(bits) html += `<div class="history-item"><span class="n">${esc(bits)}</span><span class="w">${a.source==='garmin'?'Garmin':''}</span></div>`;
    const byEx = {};
    (a.sets||[]).sort((x,y)=>x.set_no-y.set_no).forEach(s => (byEx[s.exercise] = byEx[s.exercise] || []).push(s));
    for(const [name, ss] of Object.entries(byEx)){
      const w = ss.map(s=>s.weight_kg).filter(x=>x!=null);
      const reps = ss.map(s => s.seconds!=null ? s.seconds+'s' : (s.reps ?? '–')).join(', ');
      html += `<div class="history-item"><span class="n">${esc(name)}${ss[0].done?'':' (not ticked)'}</span><span class="w">${w.length?fmtKg(Math.max(...w))+' × ':''}${esc(reps)}</span></div>`;
      if(ss[0].note) html += `<div class="history-note">${esc(ss[0].note)}</div>`;
    }
    if(a.notes) html += `<div class="history-note">${esc(a.notes)}</div>`;
    html += `<div class="history-actions"><button onclick="deleteActivity('${a.id}')">Delete</button></div></div>`;
  }
  html += `</div><p class="credit"><a onclick="showPassword()">Set a password</a> &middot; <a onclick="signOut()">Sign out</a></p>`;
  document.getElementById('root').innerHTML = html;
}
async function deleteActivity(id){
  if(!confirm('Delete this entry? This can\'t be undone.')) return;
  const { error } = await db.from('activities').delete().eq('id', id);
  if(error) return toast(error.message, true);
  await loadAll(); showHistory();
}

// ---------------------------------------------------------------- log another sport
function showLog(){
  view='log'; setTabs('log');
  const opts = SPORTS.map(([k,l]) => `<option value="${k}">${l}</option>`).join('');
  document.getElementById('root').innerHTML = back() + `
    <div class="slide">
      <h2 class="ex-name">Log an activity</h2>
      <p class="ex-detail">Runs, football, swims, climbs, anything outside the gym cards. Garmin will fill runs in automatically later.</p>
      <div class="form">
        <label>Type<select id="lg-type">${opts}</select></label>
        <label>Date<input id="lg-day" type="date" value="${todayISO()}"></label>
        <div class="form-row">
          <label>Minutes<input id="lg-min" inputmode="numeric" placeholder="45"></label>
          <label>Distance (km)<input id="lg-km" inputmode="decimal" placeholder="optional"></label>
        </div>
        <label>How hard (1–10)<input id="lg-effort" type="range" min="1" max="10" value="5" oninput="document.getElementById('lg-eff-v').textContent=this.value"><span class="range-v" id="lg-eff-v">5</span></label>
        <label>Notes<textarea id="lg-notes" rows="3" placeholder="e.g. intervals 6×800m, legs heavy after football"></textarea></label>
      </div>
      <div class="finish"><button onclick="saveLog()">Save</button></div>
    </div>`;
}
async function saveLog(){
  const v = id => document.getElementById(id).value.trim();
  const row = { type:v('lg-type'), day:v('lg-day')||todayISO(), duration_min:num(v('lg-min')), distance_km:num(v('lg-km')),
                effort:parseInt(v('lg-effort')), notes:v('lg-notes')||null, source:'manual', status:'done',
                title:(SPORTS.find(s=>s[0]===v('lg-type'))||[0,'Activity'])[1] };
  const { error } = await db.from('activities').insert(row);
  if(error) return toast(error.message, true);
  toast('Saved'); showHistory();
}

start();

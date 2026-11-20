'use strict';
const $ = (id) => document.getElementById(id);
const html = (v) => String(v === null || v === undefined ? '—' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = (v) => v === null || v === undefined || v === '' || v === '—' ? '—' : String(v);
const toNum = (v) => typeof v === 'number' && isFinite(v) ? v : null;
const intf = (v) => toNum(v) === null ? '—' : v.toLocaleString();
const watts = (v) => toNum(v) === null ? '—' : `${Number(v).toLocaleString(undefined,{maximumFractionDigits:1})} W`;
const speed = (v) => toNum(v) === null ? 'Unknown' : v >= 1000 ? `${v / 1000} Gbps` : `${v} Mbps`;
const bytes = (v) => { if(toNum(v) === null)return '—';const units=['B','KB','MB','GB','TB'];let n=v,i=0;while(n>=1024&&i<4){n/=1024;i++}return `${n.toFixed(n<10?1:0)} ${units[i]}`; };
const statetag = (s) => `<span class="state ${html(s)}">${html(s || 'unknown')}</span>`;
let state = {source:'demo',sites:[],siteId:null,snapshot:null,view:'overview',filter:'all',query:'',requestId:0};
let toastTimer;
function toast(msg, error=false){const el=$('toast');el.textContent=msg;el.className=`toast show${error?' error':''}`;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.className='toast',4200);}
async function api(path, options={}){
  const init={credentials:'same-origin',...options};
  if(options.method && options.method.toUpperCase()!=='GET'){
    init.headers={'Content-Type':'application/json','X-Requested-With':'PortScope',...(options.headers||{})};
  }
  let r;try{r=await fetch(path,init);}catch(_){throw Error('Cannot reach PortScope. Check that the app is running.');}
  let data;try{data=await r.json();}catch(_){throw Error(`Unexpected server response (${r.status}).`);}
  if(!r.ok){if(r.status===401&&path!=='/api/login'){showLogin();}throw Error(data.detail||`Request failed (${r.status})`)}
  return data;
}
function showLogin(){$('loginOverlay').hidden=false;$('appShell').hidden=true;$('connectionModal').hidden=true;$('loginPass').focus()}
function showApp(){$('loginOverlay').hidden=true;$('appShell').hidden=false;}
async function init(){
  try{const session=await api('/api/session');if(session.authenticated){showApp();await reloadSites();}else{showLogin()}}
  catch(e){showLogin();toast(e.message,true)}
}
async function reloadSites(preferId){
  const response=await api('/api/sites');
  state.sites=response.sites;state.source=response.source;
  const picker=$('siteSelect');picker.replaceChildren();
  response.sites.forEach(s=>{const option=document.createElement('option');option.value=s.id;option.textContent=s.name;picker.append(option)});
  if(!response.sites.length){state.siteId=null;state.snapshot=null;showEmpty('No sites are accessible using the current Cloud Key credentials.');return;}
  const current=preferId&&response.sites.find(s=>s.id===preferId) ? preferId : state.siteId&&response.sites.find(s=>s.id===state.siteId) ? state.siteId : response.sites[0].id;
  state.siteId=current;picker.value=current;
  await loadSnapshot();
}
function setBusy(yes){$('refreshBtn').disabled=yes;$('refreshBtn').textContent=yes?'↻ Loading…':'↻ Refresh'}
async function loadSnapshot(){
  if(!state.siteId)return;
  const current=++state.requestId;setBusy(true);
  try{
    const snap=await api(`/api/sites/${encodeURIComponent(state.siteId)}/snapshot`);
    if(current!==state.requestId)return;
    state.snapshot=snap;state.source=snap.source;
    $('timeBadge').textContent=`Synced ${new Date(snap.collected_at).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}`;
    render();
  }catch(e){if(current===state.requestId){toast(e.message,true);$('alertBox').hidden=false;$('alertBox').textContent=`Unable to refresh this site: ${e.message}`;}}
  finally{if(current===state.requestId)setBusy(false)}
}
function showEmpty(message){$('summaryGrid').replaceChildren();['switchRackList','switchDetailsList','apPreview','allAps'].forEach(id=>$(id).innerHTML=`<div class="empty-state"><strong>No devices to display</strong>${html(message)}</div>`);}
function changeView(view){
  state.view=view;
  const names={overview:'Site overview',switches:'Switchports',aps:'Access points',report:'Report center'};
  document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active-view',el.id===`${view}View`));
  $('breadcrumbName').textContent=names[view];
  $('pageTitle').textContent={overview:'Network overview',switches:'Switchport explorer',aps:'Access point inventory',report:'Site reporting'}[view];
  $('pageSubtitle').textContent={overview:'Every switch, every port, one clear view of your site.',switches:'Inspect live switch faces, PoE activity, and linked devices.',aps:'All managed access points in the selected UniFi site.',report:'Export and print a site-specific inventory snapshot.'}[view];
  if(view==='report')renderReport();
}
function setFilter(filter){state.filter=filter;document.querySelectorAll('.filter-item').forEach(e=>e.classList.toggle('active',e.dataset.filter===filter));renderSwitches();if(state.view==='aps'||state.view==='report')changeView('overview');}
function filterMatch(p){if(state.filter==='up')return p.state==='up';if(state.filter==='poe')return p.poe_enabled===true||p.poe_w>0;if(state.filter==='ap')return p.is_ap_link;if(state.filter==='errors')return (p.rx_errors||0)>0||(p.tx_errors||0)>0;return true;}
function queryMatch(sw,p,q){if(!q)return true;return [sw.name,sw.model,sw.mac,sw.ip,p.idx,p.label,p.profile,p.network,p.vlan,...p.clients.flatMap(c=>[c.name,c.ip,c.mac]),...p.neighbors.map(n=>n.name)].some(v=>String(v??'').toLowerCase().includes(q));}
function render(){
  const s=state.snapshot;if(!s)return;
  const demo=state.source==='demo';
  $('sourceBadge').textContent=demo?'● DEMO MODE':'● LIVE CONTROLLER';
  $('sourceBadge').className='chip '+(demo?'demo-chip':'live-chip');
  $('sidebarController').classList.toggle('live',!demo);
  $('sidebarController').querySelector('strong').textContent=demo?'Demo workspace':'Cloud Key connected';
  $('sidebarController').querySelector('small').textContent=demo?'Sample inventory data':'Read-only controller access';
  $('siteSubtext').textContent=`${s.summary.switches} switches · ${s.summary.aps} access points`;
  $('eyebrowSite').textContent=s.site.name.toUpperCase();
  $('reportName').textContent=`${s.site.name} · Switchport report`;
  $('reportDescription').textContent=`${new Date(s.collected_at).toLocaleString()} · ${demo?'SYNTHETIC SAMPLE DATA':'Controller-reported inventory'}`;
  $('alertBox').hidden=!demo&&!s.warnings.length;
  if(demo)$('alertBox').textContent='Demo mode: all device names, statuses and port values are synthetic. Connect to your Cloud Key to see real site inventory.';
  else if(s.warnings.length)$('alertBox').textContent=s.warnings.join(' ');
  const all=s.switches.flatMap(sw=>sw.ports);
  $('fAll').textContent=all.length;
  $('fUp').textContent=all.filter(p=>p.state==='up').length;
  $('fPoe').textContent=all.filter(p=>p.poe_enabled===true||p.poe_w>0).length;
  $('fAp').textContent=all.filter(p=>p.is_ap_link).length;
  $('fErr').textContent=all.filter(p=>(p.rx_errors||0)>0||(p.tx_errors||0)>0).length;
  const stats=[
    ['Managed switches',s.summary.switches,'▥',`${s.summary.online_switches} online`],
    ['Access points',s.summary.aps,'◉',`${s.summary.online_aps} online`],
    ['Connected ports',s.summary.active_ports,'◫',`of ${s.summary.ports} total`],
    ['Active PoE',watts(s.summary.poe_w),'ϟ','Reported power draw'],
    ['Network clients',s.summary.clients,'⌁','Active UniFi clients'],
  ];
  $('summaryGrid').innerHTML=stats.map(([name,num,glyph,footer])=>`<div class="summary-card"><div class="metric-top"><span>${html(name)}</span><span class="metric-icon">${glyph}</span></div><div class="metric-value">${html(num)}</div><div class="metric-foot">${html(footer)}</div></div>`).join('');
  renderSwitches();renderAPs();renderReport();
}
function portClass(p){if(p.state==='up'){if((p.speed||0)>=10000)return 'up fast';if((p.speed||0)>0&&(p.speed||0)<1000)return 'up slow';return 'up';}return p.state==='down'?'down':'unknown'}
function rackColumns(n){return n>=48?24:n>24?16:n>=24?12:n>=16?8:Math.max(n,4)}
function renderRack(sw,query){
  const ports=sw.ports||[];const count=ports.length;
  const cols=rackColumns(count);
  const minwidth=Math.max(320,cols*(count>24?31:43));
  const matches=ports.filter(p=>filterMatch(p)&&queryMatch(sw,p,query));
  const isRelevant=matches.length || (!query&&state.filter==='all') || (query&&[sw.name,sw.model,sw.mac,sw.ip].some(x=>String(x).toLowerCase().includes(query))&&state.filter==='all');
  if(query&&!isRelevant)return '';
  const portHTML=ports.length ? ports.map(p=>{
    const isMatch=filterMatch(p)&&queryMatch(sw,p,query);
    const emphasis=state.filter!=='all'||Boolean(query);
    const cls=emphasis ? (isMatch?'is-match':'is-dim') : '';
    const title=`${sw.name} / ${p.label} — ${p.state}, ${p.speed?speed(p.speed):'speed unknown'}`;
    return `<button class="port ${portClass(p)} ${cls}" data-port-idx="${html(p.idx)}" data-switch-mac="${html(sw.mac)}" title="${html(title)}" aria-label="${html(title)}"><span class="led"></span><span class="port-label">${html(p.idx)}</span>${(p.poe_w||0)>0?'<span class="poe-flash">ϟ</span>':''}${p.is_ap_link?'<span class="ap-ring"></span>':''}</button>`;
  }).join('') : '<div class="no-ports">No port table available from this controller</div>';
  return `<article class="switch-card"><div class="switch-top"><div class="switch-identity"><div class="switch-icon">▥</div><div><div class="switch-title">${html(sw.name)}</div><div class="switch-subline"><span>${html(sw.model)}</span><span>${html(sw.ip)}</span><span>${html(sw.mac)}</span></div></div></div><div class="switch-right">${statetag(sw.state)}<span class="switch-mini">${count} reported ports</span></div></div>
    <div class="rack"><div class="rack-top"><span>UniFi · Managed switch</span><strong>${html(sw.model)}</strong></div><div class="port-grid-scroller"><div class="port-grid" style="--cols:${cols};--minwidth:${minwidth}px">${portHTML}</div></div><div class="rack-bottom"><div class="rack-facts"><span><strong>${ports.filter(p=>p.state==='up').length}</strong> active</span><span><strong>${ports.filter(p=>p.state==='down').length}</strong> down</span><span>PoE <strong>${watts(sw.poe_total_w)}</strong></span></div><span>CLICK A PORT TO INSPECT ↗</span></div></div><div class="switch-foot"><span>Firmware <strong>${html(sw.firmware)}</strong></span><span>${html(sw.uplink?`Uplink: ${sw.uplink.device} · port ${sw.uplink.port_idx}`:'Uplink not confirmed')}</span></div></article>`;
}
function renderSwitches(){
  const snap=state.snapshot;if(!snap)return;
  const oquery=$('overviewSearch').value.trim().toLowerCase();
  const squery=$('switchSearch').value.trim().toLowerCase();
  const empty='<div class="empty-state"><strong>No matching switches or ports</strong>Try a different search or device filter.</div>';
  $('switchRackList').innerHTML=snap.switches.map(sw=>renderRack(sw,oquery)).filter(Boolean).join('')||empty;
  $('switchDetailsList').innerHTML=snap.switches.map(sw=>renderRack(sw,squery)).filter(Boolean).join('')||empty;
}
function apHTML(ap){
  return `<article class="ap-card"><div class="ap-head"><div class="ap-icon">◉</div><div style="min-width:0"><div class="ap-name">${html(ap.name)}</div><div class="ap-model">${html(ap.model)}</div></div><div class="ap-status">${statetag(ap.state)}</div></div><div class="ap-details"><div><small>IP address</small><strong>${html(ap.ip)}</strong></div><div><small>Connected clients</small><strong>${intf(ap.clients_count)}</strong></div><div><small>Firmware</small><strong>${html(ap.firmware)}</strong></div><div><small>MAC address</small><strong>${html(ap.mac)}</strong></div></div>${ap.uplink?`<button class="ap-uplink ap-link-btn" title="Inspect AP uplink port" data-port-idx="${html(ap.uplink.port_idx)}" data-switch-mac="${html(ap.uplink.device_mac)}">◈ &nbsp; Uplink: <strong>${html(ap.uplink.device)}</strong> &nbsp;·&nbsp; port ${html(ap.uplink.port_idx)} ↗</button>`:'<div class="ap-uplink">◈ &nbsp; Uplink not reported by controller</div>'}</article>`;
}
function renderAPs(){const a=state.snapshot.aps;const empty='<div class="empty-state"><strong>No access points</strong>The selected site contains no reported managed APs.</div>';$('apPreview').innerHTML=a.slice(0,3).map(apHTML).join('')||empty;$('allAps').innerHTML=a.map(apHTML).join('')||empty;$('printAps').innerHTML=a.map(apHTML).join('')||empty;}
function renderReport(){const snap=state.snapshot;if(!snap)return;const rows=[];for(const sw of snap.switches){for(const p of sw.ports){const attached=[...p.neighbors.map(n=>n.name),...p.clients.map(c=>c.name)].join(', ')||'—';const err=(p.rx_errors??0)+(p.tx_errors??0);rows.push(`<tr><td class="mini-strong">${html(sw.name)}</td><td><strong class="mini-strong">${html(p.idx)}</strong> &nbsp;${html(p.label)}</td><td class="${p.state==='up'?'good':p.state==='down'?'bad':'warn'}">${html(p.state)}</td><td>${html(p.speed?speed(p.speed):'—')}</td><td>${watts(p.poe_w)}</td><td>${html(p.profile)} / ${html(p.vlan)}</td><td>${html(attached)}</td><td>${html(err)}</td></tr>`);}}$('reportRows').innerHTML=rows.join('')||'<tr><td colspan="8">No port data was returned for this site.</td></tr>';$('reportAPRows').innerHTML=snap.aps.map(ap=>`<tr><td class="mini-strong">${html(ap.name)}</td><td>${html(ap.model)}</td><td>${html(ap.ip)}</td><td>${html(ap.state)}</td><td>${intf(ap.clients_count)}</td><td>${html(ap.uplink?`${ap.uplink.device} / port ${ap.uplink.port_idx}`:'Not confirmed')}</td></tr>`).join('')||'<tr><td colspan="6">No access points reported.</td></tr>';}
function detailRow(k,v){return `<div class="detail-row"><span>${html(k)}</span><strong>${html(label(v))}</strong></div>`;}
function openPort(swMac,idx){
  const sw=state.snapshot?.switches.find(s=>s.mac===swMac);const p=sw?.ports.find(p=>Number(p.idx)===Number(idx));if(!p)return;
  $('drawerTitle').textContent=`${sw.name} · Port ${p.idx}`;
  const neighbors=[...p.neighbors.map(n=>`<div class="neighbor"><strong>${html(n.name)}</strong><small>${html(n.kind)} · ${html(n.mac)} ${n.port?' · '+html(n.port):''}</small></div>`),...p.clients.map(c=>`<div class="neighbor"><strong>${html(c.name)}</strong><small>Wired client · ${html(c.ip)} · ${html(c.mac)}</small></div>`)];
  $('drawerContent').innerHTML=`<div class="drawer-port-hero"><div class="giant-port ${portClass(p)}">${html(p.idx)}</div><strong>${html(p.label)}</strong><p>${html(p.state.toUpperCase())} · ${html(p.speed?speed(p.speed):'Speed unknown')}</p></div><div class="detail-section"><h3>Interface information</h3>${detailRow('Switch',sw.name)}${detailRow('Model',sw.model)}${detailRow('Port index',p.idx)}${detailRow('Media',p.kind)}${detailRow('Link state',p.state)}${detailRow('Link speed',p.speed?speed(p.speed):'—')}${detailRow('Port enabled',p.enabled===null?'—':p.enabled===false?'No':'Yes')}${detailRow('Native network',p.network)}${detailRow('VLAN',p.vlan)}${detailRow('Port profile',p.profile)}${detailRow('AP connection',p.is_ap_link?'Yes':'Not observed')}${detailRow('Uplink flag',p.is_uplink?'Yes':'Not reported')}</div><div class="detail-section"><h3>Power over Ethernet</h3>${detailRow('PoE enabled',p.poe_enabled===null?'—':p.poe_enabled?'Yes':'No')}${detailRow('PoE mode',p.poe_mode)}${detailRow('PoE consumption',watts(p.poe_w))}</div><div class="detail-section"><h3>Port counters (cumulative)</h3>${detailRow('RX bytes',bytes(p.rx_bytes))}${detailRow('TX bytes',bytes(p.tx_bytes))}${detailRow('RX errors',intf(p.rx_errors))}${detailRow('TX errors',intf(p.tx_errors))}</div><div class="detail-section"><h3>Detected neighbors & clients</h3>${neighbors.join('')||'<p style="font-size:12px;color:#8b98b1">No confirmed device association reported for this port.</p>'}</div>`;
  $('detailShade').hidden=false;$('portDrawer').hidden=false;$('drawerClose').focus();
}
function closePort(){$('detailShade').hidden=true;$('portDrawer').hidden=true;}
function showModal(){$('connectionModal').hidden=false;$('connectionError').hidden=true;api('/api/connection').then(c=>{if(c.url)$('controllerUrl').value=c.url;if(c.mode)$('controllerMode').value=c.mode;if(c.auth)$('authMode').value=c.auth;$('tlsVerify').checked=c.verify;toggleAuth();toggleTLS();}).catch(e=>toast(e.message,true));}
function closeModal(){$('connectionModal').hidden=true;}
function toggleAuth(){$('keyFields').hidden=$('authMode').value!=='api_key';$('passwordFields').hidden=$('authMode').value!=='password';}
function toggleTLS(){$('tlsWarning').hidden=$('tlsVerify').checked;}
async function connect(e){
  e.preventDefault();const btn=$('testConnectBtn');btn.disabled=true;btn.textContent='Connecting…';$('connectionError').hidden=true;
  try{const body={url:$('controllerUrl').value.trim(),mode:$('controllerMode').value,auth:$('authMode').value,api_key:$('apiKey').value,username:$('unifiUsername').value,password:$('unifiPassword').value,verify:$('tlsVerify').checked};
    const r=await api('/api/connection',{method:'POST',body:JSON.stringify(body)});
    $('apiKey').value='';$('unifiPassword').value='';closeModal();state.siteId=null;await reloadSites();toast(`Connected to Cloud Key (${r.mode}). ${r.sites.length} site(s) found.`);
  }catch(err){$('connectionError').textContent=err.message;$('connectionError').hidden=false;toast('Connection failed. Check the details in the dialog.',true)}
  finally{btn.disabled=false;btn.textContent='Connect & load sites';}
}
async function returnDemo(){if(!confirm('Disconnect the controller and return to synthetic demo inventory?'))return;try{await api('/api/demo',{method:'POST',body:'{}'});closeModal();state.siteId=null;await reloadSites();toast('Demo data restored.');}catch(e){toast(e.message,true)}}
async function exportReport(fmt){if(!state.siteId)return;const path=`/api/sites/${encodeURIComponent(state.siteId)}/export.${fmt}`;window.location.assign(path);}
function wire(){
  $('loginForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/login',{method:'POST',body:JSON.stringify({password:$('loginPass').value})});$('loginPass').value='';showApp();await reloadSites();}catch(err){toast(err.message,true)}});
  $('siteSelect').addEventListener('change',e=>{state.siteId=e.target.value;loadSnapshot();});
  $('mainNav').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(b)changeView(b.dataset.view)});
  $('filterNav').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(b)setFilter(b.dataset.filter)});
  $('overviewSearch').addEventListener('input',renderSwitches);
  $('switchSearch').addEventListener('input',renderSwitches);
  document.addEventListener('click',e=>{const p=e.target.closest('[data-port-idx]');if(p)openPort(p.dataset.switchMac,p.dataset.portIdx)});
  $('drawerClose').addEventListener('click',closePort);$('detailShade').addEventListener('click',closePort);
  $('refreshBtn').addEventListener('click',loadSnapshot);
  $('seeAps').addEventListener('click',()=>changeView('aps'));
  ['connectionBtn','sidebarConnect'].forEach(id=>$(id).addEventListener('click',showModal));
  ['modalClose','cancelConnect'].forEach(id=>$(id).addEventListener('click',closeModal));
  $('connectForm').addEventListener('submit',connect);
  $('authMode').addEventListener('change',toggleAuth);$('tlsVerify').addEventListener('change',toggleTLS);
  $('demoModeBtn').addEventListener('click',returnDemo);
  ['printBtn','reportPrint'].forEach(id=>$(id).addEventListener('click',()=>window.print()));
  ['exportBtn','reportCSV'].forEach(id=>$(id).addEventListener('click',()=>exportReport('csv')));
  $('reportJSON').addEventListener('click',()=>exportReport('json'));
  $('logoutBtn').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});state.snapshot=null;showLogin();toast('Signed out.')}catch(e){toast(e.message,true)}});
  window.addEventListener('keydown',e=>{if(e.key==='Escape'){closePort();closeModal();}});
}
wire();init();

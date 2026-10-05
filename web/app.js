import {createInventoryUI} from './inventory.js';
import {storageUI} from './storage.js';
import {createDustUI} from './dust.js';
import {forecastForm,forecastHTML} from './longterm.js';
const STAT={critRate_:'暴击率',critDMG_:'暴击伤害',atk_:'百分比攻击',atk:'固定攻击',enerRech_:'元素充能',eleMas:'元素精通',hp_:'百分比生命',hp:'固定生命',def_:'百分比防御',def:'固定防御',heal_:'治疗加成',physical_dmg_:'物伤',pyro_dmg_:'火伤',hydro_dmg_:'水伤',cryo_dmg_:'冰伤',electro_dmg_:'雷伤',anemo_dmg_:'风伤',geo_dmg_:'岩伤',dendro_dmg_:'草伤'};
const SLOT={flower:'花',plume:'羽',sands:'沙',goblet:'杯',circlet:'冠'};
const WEIGHTS=['critRate_','critDMG_','atk_','atk','enerRech_','eleMas','hp_','hp','def_','def'];
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=x=>JSON.parse(JSON.stringify(x));
let catalog,token,draft,active=0,configId=null,latestResult=null,busy=false,dragId=null,toastTimer,renderStamp='';
let autosaveTimer,draftSequence=Date.now(),draftClient,contextTime=0;
let lastElixir=null,activeElixirJob=null,dustUI=null,renderForecast=null,lastEmergencyAt=0;
let storagePanel=null,inventoryPanel=null,selectedPreset=null,storageInstance='default';
const cacheKey=name=>`artifact-workbench-${storageInstance}-${name}-v2`;

function elixirOptions(){return draft.elixir??=( {budget:4,remaining_by_set:{},minimum_gain:0,objective:'expected_gain',respect_priority:true} );}
function renderElixirForm(){
  if(!draft||!catalog)return;
  renderForecast?.();
  const e=elixirOptions(),p=profile(),entries=draft.mode==='single'?[draft.demands[active]]:draft.demands;
  $('elixir-target').innerHTML=entries.map(d=>`<option value="${esc(d.id)}">${esc(d.profile.character)} · ${esc(d.profile.name)}</option>`).join('');
  $('elixir-target').value=draft.demands[active].id;
  $('elixir-snapshot').innerHTML=$('snapshot').innerHTML;$('elixir-snapshot').value=$('snapshot').value;
  $('elixir-snapshot-note').textContent=$('snapshot-note').textContent;
  $('elixir-goal').value=p.resource_target_score??'';$('elixir-compare').checked=!!e.compare_dust;
  const advanced=document.querySelector('[data-advanced=elixir]');if(advanced&&(p.resource_target_score!=null||e.compare_dust||e.minimum_gain>0||!e.respect_priority))advanced.open=true;
  $('elixir-budget').value=e.budget;$('elixir-quota').value=e.remaining_by_set[p.set_key]??2;
  $('elixir-minimum').value=e.minimum_gain;$('elixir-objective').value=e.objective;$('elixir-priority').checked=e.respect_priority;
  $('elixir-profile-note').textContent=`${catalog.sets[p.set_key]||p.set_key} · 4+1 · 暴击率计分上限 ${p.artifact_crit_rate_cap}% · ${draft.equipment==='borrow'?'允许借用':'不借用其他角色装备'}。评分和主属性沿用培养配置。`;
}
const ELIXIR_OBJECTIVES={expected_gain:'平均提升优先',probability:'成功概率优先',efficiency:'每个霜收益优先',target_probability:'达标概率优先',target_gain:'目标内收益优先'};
function elixirActionName(plan){return plan.actions.map(a=>`${SLOT[a.slot]} · ${STAT[a.main]||a.main}（${a.selected.map(k=>STAT[k]||k).join('＋')}）`).join(' / ');}
const range=(a,b,digits=3,scale=1)=>`${(a*scale).toFixed(digits)}–${(b*scale).toFixed(digits)}`;
export function elixirResultHTML(result,objective='expected_gain'){
  if(!result)return '';
  const ranks=result.rankings||{},plans=ranks[objective]||[];
  const cards=Object.entries(ELIXIR_OBJECTIVES).map(([key,title])=>{
    const p=ranks[key]?.[0];if(!p)return '';
    const value=key==='target_probability'?range(p.target_probability_lower,p.target_probability_upper,1,100)+'%':key==='target_gain'?range(p.target_gain_lower,p.target_gain_upper)+' 分':key==='probability'?range(p.probability_lower,p.probability_upper,1,100)+'%':key==='efficiency'?(p.expected_gain_lower/p.cost).toFixed(3)+' 分/霜':range(p.expected_gain_lower,p.expected_gain_upper)+' 分';
    return `<article class="panel elixir-card ${key===objective?'is-selected':''}" data-objective="${key}"><span class="tag">${title}</span><h3>${esc(elixirActionName(p))}</h3><div class="score">${value}</div><p>${p.cost} 个霜 · ${p.definitions} 次定制${p.method==='sampled_fixed_pair'?' · 固定双件候选':''}</p></article>`;
  }).join('');
  const details=plans.length?`<details class="panel comparison-panel"><summary>查看候选明细 · ${ELIXIR_OBJECTIVES[objective]}</summary><div class="table-wrap"><table><thead><tr><th>选择</th><th>霜 / 次数</th><th>提升概率</th><th>平均提升</th><th>每霜提升</th><th>计算方式</th><th>达标概率 / 目标内收益</th></tr></thead><tbody>${plans.map(p=>`<tr><td>${esc(elixirActionName(p))}</td><td>${p.cost} / ${p.definitions}</td><td>${range(p.probability_lower,p.probability_upper,2,100)}%</td><td>${range(p.expected_gain_lower,p.expected_gain_upper,4)}</td><td>${(p.expected_gain_lower/p.cost).toFixed(4)}</td><td>${p.method==='enumerated'?'离散枚举':`20万组双件模拟<br><small>平均提升95%半宽 ±${p.metrics.lo.expected_gain_95_halfwidth.toFixed(4)}</small>`}</td><td>${p.target_gain_lower==null?'未设置目标':range(p.target_probability_lower,p.target_probability_upper,1,100)+'%<br>'+range(p.target_gain_lower,p.target_gain_upper)+' 分'}</td></tr>`).join('')}</tbody></table></div></details>`:'';
  const c=result.configuration||{};
  return `<article class="panel"><h2>${esc(result.character)} · ${esc(result.set_label)}</h2><p class="small muted">${esc(result.demand_name)}${result.snapshot?` · 库存 ${esc(result.snapshot.date)} · ${esc(result.snapshot.label)}`:''}${result.calculated_at?` · 计算于 ${esc(new Date(result.calculated_at*1000).toLocaleString())}`:''}</p><div class="elixir-meta"><span>基准 ${range(...result.baseline,4)} 分</span><span>预算 ${result.options.budget} 个霜</span><span>剩余 ${result.remaining} 次</span><span>最低提升 ${result.options.minimum_gain} 分</span><span>定制四词条 1/3</span><span>保留高优先级装备 ${result.reserved_count} 件</span></div><p class="small muted">目标 ${c.target_score??'未设置'} 分 · 暴击率计分上限 ${c.crit_cap}% · ${c.equipment==='borrow'?'允许借用':'不借用'} · ${c.allocation==='priority'?'按配置优先级与场景':'独立优化当前角色'}。分数单位为平均词条，不是伤害百分比。</p>${result.message?`<p class="warning">${esc(result.message)}</p>`:''}</article><div class="elixir-cards">${cards}</div>${details}${resourceComparisonHTML(result.resource_comparison)}${forecastHTML(result.longterm,STAT,SLOT,"霜")}<details class="panel note-panel"><summary>计算范围与结果说明</summary><p>${esc(result.scope)}</p><p>结果使用本次保存的配置与库存；修改预算、次数或角色规则后请重新计算。做完第一件后，应更新库存再推荐第二件。单次区间来自库存显示取整；双件另有模拟误差，区间不包含所有机制参数的不确定性。</p></details>`;
}
function renderElixirResult(result){lastElixir=result;$('elixir-results').className='';$('elixir-results').innerHTML=elixirResultHTML(result,elixirOptions().objective);$('elixir-export').disabled=false;}
function resourceComparisonHTML(c){
  if(!c)return '';
  const rows=[...c.elixir.map(a=>({a,resource:'霜',name:elixirActionName(a)})),...c.dust.map(a=>({a,resource:'尘',name:`${a.name} · ${a.selected.map(k=>STAT[k]||k).join('＋')}`}))];
  return `<article class="panel"><h2>换胚还是重塑 · 单次机会对照</h2><p class="small muted">${esc(c.note)} · 尘预算 ${c.dust_options.budget} · 进度 ${c.dust_options.points}/6 · 已触发高阶 ${c.dust_options.phase} 次</p><div class="table-wrap"><table><thead><tr><th>选择</th><th>材料</th><th>提升概率</th><th>平均提升</th><th>达标概率 / 目标内收益</th></tr></thead><tbody>${rows.map(({a,resource,name})=>`<tr><td>${esc(name)}</td><td>${a.cost} 个${resource}</td><td>${range(a.probability_lower,a.probability_upper,2,100)}%</td><td>${range(a.expected_gain_lower,a.expected_gain_upper,4)}</td><td>${a.target_gain_lower==null?'未设置目标':range(a.target_probability_lower,a.target_probability_upper,1,100)+'%<br>'+range(a.target_gain_lower,a.target_gain_upper)+' 分'}</td></tr>`).join('')}</tbody></table></div><p class="small muted">${c.deferred_count} 件重塑底子待核验。没有自动定制或重塑动作。</p></article>`;
}
async function launchElixir(){
  if(!validateForm($('elixir-form')))throw Error('请填写有效的预算与最低提升');
  if(elixirOptions().compare_dust){draft.dust??={budget:0,points:0,phase:0,objective:'efficiency',respect_priority:true,metadata:{}};draft.dust.respect_priority=elixirOptions().respect_priority;}
  const demand=draft.demands[active].id,config_id=await saveCurrent();
  await api('/api/jobs',{kind:'elixir',config_id,demand_id:demand,snapshot_id:$('snapshot').value});
  lastElixir=null;renderStamp='';$('elixir-export').disabled=true;$('elixir-results').innerHTML='<div class="panel"><p>正在根据所选库存计算…</p></div>';
  showPage('elixir');await refreshState();
}

export function serializeDraft(d,selected=0){
  const out=copy(d);if(out.mode==='single'){out.demands=[out.demands[selected]];out.scenarios=null;out.allocation='priority';}
  return out;
}
export function moveDemand(d,from,to){
  if(to<0||to>=d.demands.length)return from;
  const [item]=d.demands.splice(from,1);d.demands.splice(to,0,item);return to;
}
export function probabilityPercent(value){return Number(value)*100;}
export function importDraft(value){
  if(value.character&&value.main_stats&&value.weights){
    return {version:1,name:value.name||value.character+'培养计划',mode:'single',
      equipment:value.allowed_equipped_characters?.includes('*')?'borrow':'protected',allocation:'priority',
      demands:[{id:'imported-profile',profile:value}],scenarios:null};
  }
  return value;
}
function context(){return {config:draft,active_index:active,config_id:configId,ui:{fresh:$('fresh-scan').checked,snapshot_id:$('snapshot').value}};}
async function flushDraft(){
  if(!token||!draftClient)return;
  const sequence=draftSequence=Math.max(Date.now(),draftSequence+1);
  try{const r=await api('/api/draft',{client:draftClient,sequence,payload:context()});if(!r.ignored)$('save-state').textContent=`已自动保存到本地数据库 · v${r.revision}`;}
  catch(e){$('save-state').textContent='浏览器草稿已保存；数据库同步失败';}
}
function persist(){contextTime=Date.now();try{localStorage.setItem(cacheKey('draft'),JSON.stringify(draft));localStorage.setItem(cacheKey('context'),JSON.stringify({...context(),updated:contextTime}));}catch{}$('save-state').textContent='草稿已保存，正在同步数据库…';clearTimeout(autosaveTimer);autosaveTimer=setTimeout(flushDraft,650);}
function changed(){persist();renderDemands();}
function notify(message,error=false){const el=$('toast');el.textContent=message;el.className=error?'error':'';el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,error?7000:3500);}
async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'本地服务请求失败');return data;}
function protect(fn){return async(...args)=>{try{return await fn(...args);}catch(e){notify(e.message,true);}};}
function validateForm(form){
  const invalid=form.querySelector(':invalid');
  if(invalid){
    const page=form.closest('.page');if(page&&!page.classList.contains('active'))showPage(page.id.slice(5));
    for(let parent=invalid.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  }
  return form.reportValidity();
}
function download(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name.replace(/[\\/:*?"<>|]/g,'_')+'.json';a.click();URL.revokeObjectURL(url);}
function profile(){return draft.demands[active].profile;}
function showPage(name){
  if(!document.getElementById('page-'+name))return;
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id==='page-'+name));
  document.querySelectorAll('.nav').forEach(n=>{const selected=n.dataset.page===name;n.classList.toggle('active',selected);if(selected)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});
  const names={inventory:'库存导入',configure:'角色与预设',run:'运行与结果',elixir:'祝圣之霜',dust:'启圣之尘',loadouts:'我的配装',engine:'识别与运行',guide:'使用指南',storage:'数据管理'};
  $('current-page-title').textContent=names[name]||name;
  if(name==='inventory')inventoryPanel?.render();if(name==='storage')storagePanel?.refresh();if(name==='elixir')renderElixirForm();if(name==='dust')dustUI?.form();
  window.history.replaceState(null,'','#'+name);
}
function makeDemand(p){return {id:'d-'+Math.random().toString(36).slice(2,10),profile:copy(p)};}
function renderDemands(){
  const multi=draft.mode==='multi';const entries=multi?draft.demands:[draft.demands[active]];
  $('demand-count').textContent=entries.length;$('demand-heading').textContent=multi?'需求优先级':'培养目标';
  $('priority-hint').textContent=multi?'从上到下依次优先。可拖动或用箭头调整。':'每个需求有独立的套装和评分。';
  $('add-demand').hidden=!multi;$('scenario-area').hidden=!multi;$('allocation').disabled=!multi;
  $('mode-single').classList.toggle('selected',!multi);$('mode-multi').classList.toggle('selected',multi);
  $('mode-single').setAttribute('aria-pressed',String(!multi));$('mode-multi').setAttribute('aria-pressed',String(multi));
  $('mode-description').textContent=multi?'扫描一次，按优先级处理全部需求':'针对一个角色的一套配装';
  $('demand-list').innerHTML=entries.map(entry=>{const i=draft.demands.indexOf(entry),p=entry.profile;return `<div class="demand-card ${i===active?'active':''}" data-demand="${esc(entry.id)}" draggable="${multi}" tabindex="0" role="button" aria-label="编辑${esc(p.character)}"><div class="demand-top"><span class="rank">${String(i+1).padStart(2,'0')}</span><h3>${esc(p.character||'未命名角色')}</h3></div><p>${esc(catalog.sets[p.set_key]||p.set_key)}</p><div class="demand-bottom"><span>阈值 ${+(p.threshold*100).toFixed(2)}% · 暴击计分 ${p.artifact_crit_rate_cap}%</span>${multi?`<button class="icon-btn" data-move="-1" title="提高优先级" aria-label="提高优先级">↑</button><button class="icon-btn" data-move="1" title="降低优先级" aria-label="降低优先级">↓</button><button class="icon-btn" data-remove="1" title="删除需求" aria-label="删除需求">×</button>`:''}</div></div>`;}).join('');
}
function renderEditor(){
  const p=profile();$('editor-kicker').textContent='DEMAND '+String(active+1).padStart(2,'0');$('editor-title').textContent=p.character||'培养需求';
  $('character').value=p.character;$('profile-name').value=p.name;$('aliases').value=(p.character_aliases||[]).filter(a=>a!==p.character).join('，');$('set-key').value=p.set_key;
  $('threshold').value=+(p.threshold*100).toFixed(6);$('crit-cap').value=p.artifact_crit_rate_cap;
  $('main-stats').innerHTML=['sands','goblet','circlet'].map(slot=>`<div class="main-card"><strong>${SLOT[slot]}</strong><div class="chips">${catalog.main_options[slot].map(key=>`<button type="button" class="chip ${p.main_stats[slot].includes(key)?'on':''}" data-slot="${slot}" data-stat="${key}" aria-pressed="${p.main_stats[slot].includes(key)}">${esc(STAT[key]||key)}</button>`).join('')}</div></div>`).join('');
  $('weights').innerHTML=WEIGHTS.map(key=>`<label class="weight-box"><span>${STAT[key]}</span><input required type="number" min="0" max="100" step="0.01" data-weight="${key}" aria-label="${STAT[key]}权重" value="${p.weights[key]??0}"><small>均值 ${catalog.means[key]}${key.endsWith('_')?'%':''}</small></label>`).join('');
}
function renderScenes(){
  $('custom-scenes').checked=draft.scenarios!==null;$('add-scene').hidden=draft.scenarios===null;
  $('scenes').innerHTML=(draft.scenarios||[]).map((s,i)=>`<div class="scene"><div class="scene-head"><input data-scene-name="${i}" aria-label="场景名称" value="${esc(s.name)}"><button class="icon-btn" data-remove-scene="${i}" aria-label="删除场景">×</button></div><div class="scene-checks">${draft.demands.map(d=>`<label class="check-line"><input type="checkbox" data-scene="${i}" data-id="${d.id}" ${s.demands.includes(d.id)?'checked':''}>${esc(d.profile.character)} · ${esc(d.profile.name)}</label>`).join('')}</div></div>`).join('');
  $('auto-equip-after').checked=!!draft.auto_equip_after;
  $('equip-scene').hidden=!(draft.auto_equip_after&&draft.scenarios?.length>1&&draft.mode==='multi');
  $('equip-scene').innerHTML='<option value="">选择完成后穿戴的场景…</option>'+(draft.scenarios||[]).map((s,i)=>`<option value="${i}">${esc(s.name)}</option>`).join('');
  $('equip-scene').value=draft.equip_scene??'';
}
function renderDraft(){active=Math.min(active,draft.demands.length-1);$('config-name').value=draft.name;$('equipment').value=draft.equipment;$('allocation').value=draft.allocation;renderDemands();renderEditor();renderScenes();renderElixirForm();}
function renderPresetOptions(){
  $('template-select').innerHTML='<option value="">选择角色预设…</option><optgroup label="我的预设">'+(catalog.presets||[]).map(p=>`<option value="user:${esc(p.id)}">${esc(p.data.character)} · ${esc(p.data.name)}</option>`).join('')+'</optgroup><optgroup label="内置示例">'+catalog.profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.data.name)}</option>`).join('')+'</optgroup>';
  $('character-options').innerHTML=(catalog.characters||[]).map(n=>`<option value="${esc(n)}"></option>`).join('');
}
function renderCatalog(){
  const snapshot=$('snapshot').value;
  $('snapshot').innerHTML=catalog.snapshots.length?catalog.snapshots.map(s=>`<option value="${s.id}">${s.date} · ${s.five_star} 件五星${s.source==='import'?' · JSON 导入':s.derived?' · 记录快照':''}</option>`).join(''):'<option value="">请先导入或扫描库存</option>';
  if(catalog.snapshots.some(s=>s.id===snapshot))$('snapshot').value=snapshot;
  $('saved-configs').innerHTML='<option value="">打开已保存配置…</option>'+catalog.configs.map(c=>`<option value="${c.id}">${esc(c.name)} · ${c.count}需求</option>`).join('');
  renderLibrary();snapshotNote();renderElixirForm();renderPresetOptions();inventoryPanel?.render();
}
function snapshotNote(){const s=catalog.snapshots.find(s=>s.id===$('snapshot').value);$('snapshot-note').textContent=!s?'请先到“库存导入”页选择 JSON 或扫描库存。':s.ownership_stale?'此快照之后有换装记录，装备归属可能过期；不借用模式需重新扫描。':s.source==='import'?`已选 JSON 库存：${s.five_star} 件五星。结果仅针对文件内库存；缺少状态的信息会保留待核验。`:'可用于离线计算。若在任务外变更了库存，请重新导入或扫描。';$('snapshot-note').className='small '+(s?.ownership_stale?'warning':'muted');}
function renderLibrary(){
  $('loadout-list').innerHTML=catalog.loadouts.length?catalog.loadouts.map(l=>`<article class="panel loadout-card"><label class="check-line"><input type="checkbox" value="${esc(l.id)}" class="loadout-check">${esc(l.character)}<span class="tag">v${l.revision}</span></label><p class="small muted">${esc(l.name)}</p>${l.items.map(a=>`<div class="loadout-item"><span>${SLOT[a.attributes.slot]}</span><span>${esc(a.name)}</span><small>+${a.attributes.level} · ${STAT[a.attributes.main]||a.attributes.main}</small></div>`).join('')}</article>`).join(''):'<div class="empty-state"><h3>还没有保存配装</h3><p>完成只计算后，点击“保存为本地配装”。</p></div>';
}
function itemTable(items){return `<div class="table-wrap"><table><thead><tr><th>部位</th><th>圣遗物</th><th>主属性</th><th>当前装备者（快照）</th></tr></thead><tbody>${items.map(a=>`<tr><td>${SLOT[a.slot]||'—'}</td><td>${esc(a.name||a.id)}${a.substats?`<br><small>${a.substats.map(s=>esc(STAT[s.key]||s.key)+' '+s.value).join(' · ')}</small>`:''}</td><td>${esc(STAT[a.main]||a.main||'—')}</td><td>${esc(a.equipped||'闲置')}</td></tr>`).join('')}</tbody></table></div>`;}
function renderResults(result){
  if(!result)return;
  latestResult=result;$('download-result').disabled=false;
  if(result.kind==='dust'){dustUI.render(result);$('results').className='panel';$('results').textContent='启圣之尘建议已保存，请在左侧“启圣之尘”页查看。';return;}
  if(result.kind==='elixir'){renderElixirResult(result);$('results').className='panel';$('results').textContent='祝圣之霜建议已保存，请在左侧“祝圣之霜”页查看。';return;}
  if(result.backend==='GOODScanner'&&result.health){$('results').className='panel';$('results').textContent=`GOODScanner 已连接 · ${result.health.gameAlive?'检测到游戏窗口':'游戏未运行（连接检查仍有效）'} · 本次没有游戏输入。`;return;}
  const rows=result.demands;
  let html='';
  if(rows){html=rows.map(r=>`<article class="panel result-card"><div class="panel-heading"><div><h2>${esc(r.name||r.character)}</h2><p class="small muted">${esc(r.id)} · ${r.status==='ready'?'完整配装':'尚未配齐'}</p></div><div class="score">${r.score==null?'—':r.score.toFixed(4)} <small>分</small></div></div><div class="result-meta"><span>达标候选 ${r.eligible_count??0}</span><span>待确认 ${r.deferred_count??0}</span><span>独立上限 ${r.independent_score?.toFixed(4)??'—'}</span>${r.gain!=null?`<span>变化 ${r.gain>=0?'+':''}${r.gain.toFixed(4)}</span>`:''}</div>${itemTable(r.items||[])}</article>`).join('');
    if(result.transfers?.length)html+=`<div class="panel"><h3>让装关系</h3>${result.transfers.map(t=>`<p class="small">${esc(t.from_character)}${t.outside_list?'（名单外）':''} → ${esc(t.to_demand)}：${esc(t.name)}</p>`).join('')}</div>`;
    if(result.conflicts?.length)html+=`<p class="warning">${result.conflicts.length} 件装备存在同时使用冲突。</p>`;
  }else if(result.scores){html=`<article class="panel"><h2>强化前后</h2><div class="table-wrap"><table class="score-table"><thead><tr><th>模式</th><th>起点</th><th>当前</th><th>提升</th></tr></thead><tbody>${Object.entries(result.scores).map(([k,s])=>`<tr><td>${k==='borrow'?'允许借用':'不借用'}</td><td>${s.before?.toFixed(4)??'—'}</td><td>${s.after?.toFixed(4)??'—'}</td><td>${s.gain?.toFixed(4)??'—'}</td></tr>`).join('')}</tbody></table></div><p class="small muted">达标候选 ${result.remaining_count??0} · 待确认 ${result.deferred_count??0} · 强化确认 ${result.confirmations??0} 次</p></article>`;
  }else if(result.scan_directory){html='<div class="panel"><h2>扫描完成</h2><p class="muted">库存已保存，可以回到配置页预览或复用本次扫描。</p></div>';
  }else if(result.error){html=`<div class="panel"><h2>需要处理</h2><p class="warning">${esc(result.error)}</p></div>`;}
  if(result.equipment?.length)html+=result.equipment.map(r=>`<article class="panel result-card"><h2>${esc(r.character)} · 穿戴核验</h2><p class="small muted">已核验 ${r.verified_slots}/5 部位 · 实际变更 ${r.changed} 件</p>${r.items.map(a=>`<p class="small">${SLOT[a.slot]} · ${esc(a.name)} · ${a.status==='equipped'?'已穿戴':'原本正确'}</p>`).join('')}</article>`).join('');
  if(html){$('results').className='';$('results').innerHTML=(result.snapshot?.ownership_stale?'<div class="panel note-panel"><p class="warning">这是历史快照计算结果。快照之后发生过换装，表中的装备者与让装关系可能已变化；不借用规划需要新的归属数据。</p></div>':'')+html;}
}
function detailFromLogs(logs){for(let i=logs.length-1;i>=0;i--){try{const x=JSON.parse(logs[i]);if(['controller_starting','controller_ready','good_ready'].includes(x.phase))return x.message;if(x.phase==='dust')return `启圣之尘：已分析 ${x.done}/${x.total} 件，不操作游戏。`;if(x.phase==='elixir')return `祝圣之霜：已枚举 ${x.done}/${x.total} 种选择，不操作游戏。`;if(x.phase==='elixir_pairs')return '正在比较两次定制的固定候选方案，不操作游戏。';if(x.level!==undefined&&x.probability_before!==undefined)return `当前已到 +${x.level}，此前改善概率 ${(x.probability_before*100).toFixed(2)}%。`;if(x.eligible!==undefined)return `库存重算：${x.eligible} 件候选达到概率阈值。`;if(x.phase==='equipping_final_builds')return '强化已结束，正在按优先级穿戴最终配装。';if(x.stopped)return '单件决策已结束，正在更新库存并重算下一件。';if(x.phase==='planning')return `正在为 ${x.demands} 个需求计算配装与强化概率，本阶段不操作游戏。`;if(x.phase==='scanning')return x.backend==='GOODScanner'?`GOODScanner 正在扫描五星库存：${x.completed??0} / ${x.total||'读取中'}。`:'正在扫描库存…';if(x.phase==='good_equipment')return `GOODScanner · ${x.stage==='preflight'?'核验五件是否存在':x.stage==='verify'?'复核属性和归属':'执行穿戴'} · ${x.completed??0}/${x.total||5}`;if(x.phase==='enhancing'&&x.demand)return `正在处理需求 ${x.demand}，脚本自行选择候选并逐阶段决策。`;if(x.scan_finished)return '扫描完成，正在计算最优配装。';if(x.preflight)return `换装预检：${x.preflight}`;if(x.slot&&x.status)return `部位 ${SLOT[x.slot]}：${x.status==='equipped'?'穿戴已核验':'原本正确，跳过'}`;}catch{}}return '本地脚本正在处理，请保持游戏界面可用。';}
async function refreshState(){
  try{const state=await api('/api/state');busy=state.busy;inventoryPanel?.state(busy);dustUI?.state(state);if(state.hotkey?.last_stop_requested>lastEmergencyAt){lastEmergencyAt=state.hotkey.last_stop_requested;notify('已紧急中断当前任务；未确认记录已保留');}$('hotkey-status').textContent=[state.hotkey?.win_registered?'Win键：有任务时中断，保留系统功能':'',state.hotkey?.registered?state.hotkey.shortcut+'：全局紧急中断':'',state.hotkey?.error||''].filter(Boolean).join(' · ')||'全局快捷键未启用；可使用紧急中断按钮';
    if(state.timings?.length)$('timings').innerHTML=`<table><thead><tr><th>读取类型</th><th>次数</th><th>平均</th><th>P90</th></tr></thead><tbody>${state.timings.map(t=>`<tr><td>${esc(t.name)}</td><td>${t.count}</td><td>${t.average_ms} ms</td><td>${t.p90_ms} ms</td></tr>`).join('')}</tbody></table>`;
    $('connection-dot').className='online';$('connection').textContent=busy?'本地任务运行中':'本地服务已连接';
    for(const id of ['scan-only','preview','start','equip-loadouts','elixir-calculate','save-preset','template-select'])$(id).disabled=busy;
    $('resume').disabled=busy||!state.can_resume;$('stop').disabled=!busy;
    const job=state.job;activeElixirJob=busy&&job?.kind==='elixir'?job.id:null;$('elixir-stop').hidden=!activeElixirJob;if(!job)return;
    document.querySelector('.steps').hidden=['elixir','dust'].includes(job.kind);
    if(job.kind==='elixir'){
      let detail='正在读取库存并计算基准配装…';
      for(const line of [...job.logs].reverse()){try{const x=JSON.parse(line);if(x.phase==='longterm'){detail=`长期对照：已模拟 ${x.done}/${x.total} 条未来路径`;break;}if(x.phase==='elixir'){detail=`${x.character}：已枚举 ${x.done}/${x.total} 种单次选择`;break;}if(x.phase==='elixir_pairs'){detail='正在联合比较两次定制候选…';break;}}catch{}}
      $('elixir-status').textContent=job.status==='running'?detail:job.status==='completed'?'建议已计算并保存，可切换优先目标查看。':job.status==='stopped'?'本次计算已停止，设置已保留。':job.result?.error||'计算失败，请查看运行日志。';
    }
    const labels={running:'任务正在运行',completed:'任务完成',deferred:'已结束，有待处理项',failed:'任务已停止，需要处理',stopped:'已停止，进度保留'};
    const kind={preview:'离线配装计算',scan:'全库扫描',start:'序贯强化',resume:'继续强化',equip:'保存配装穿戴',elixir:'祝圣之霜建议',dust:'启圣之尘建议'};
    $('run-title').textContent=labels[job.status]||job.status;$('run-dot').className='status-dot '+job.status;
    const sec=Math.floor(((job.finished||Date.now()/1000)-job.started));$('elapsed').textContent=`${kind[job.kind]} · ${Math.floor(sec/60)}分${sec%60}秒`;
    $('run-detail').textContent=job.status==='running'?detailFromLogs(job.logs):job.status==='failed'?(job.result?.error||'请展开日志查看原因。未确认的操作不会自动重复提交。'):job.status==='deferred'?'可处理候选已结束，仍有概率边界或缺少基准的需求。':job.status==='stopped'?'可以在条件恢复后继续未完成的强化任务。':'结果与记录已保存到本机。';
    $('run-progress').hidden=!busy;$('logs').textContent=job.logs.join('\n')||'等待脚本输出…';
    $('save-loadouts').disabled=busy||!job.result?.demands||!['completed','deferred'].includes(job.status);
    const stamp=JSON.stringify(job.result);if(stamp!==renderStamp){renderStamp=stamp;renderResults(job.kind==='elixir'&&job.result?.kind==='elixir'?{...job.result,calculated_at:job.finished}:job.result);}
    if(!busy&&window.__lastBusy){catalog=await api('/api/catalog');renderCatalog();}
    window.__lastBusy=busy;
  }catch(e){$('connection-dot').className='';$('connection').textContent='本地服务未连接';}
}
async function saveCurrent(){if(!validateForm($('profile-form')))throw Error('请补全有效的数字与角色配置');const result=await api('/api/config',{id:configId,config:serializeDraft(draft,active)});configId=result.id;persist();$('save-state').textContent='已保存到本地数据库';return result.id;}
async function launch(kind){
  if(['preview','equip'].includes(kind)&&!$('snapshot').value){showPage('inventory');throw Error('请先导入或扫描库存');}
  const data={kind};if(['start','preview'].includes(kind))data.config_id=await saveCurrent();
  if(['start','preview','equip'].includes(kind))data.snapshot_id=$('snapshot').value;
  if(kind==='start')data.fresh=$('fresh-scan').checked;
  if(kind==='equip')data.ids=[...document.querySelectorAll('.loadout-check:checked')].map(x=>x.value);
  await api('/api/jobs',data);renderStamp='';latestResult=null;$('results').className='empty-state';$('results').innerHTML='<h3>正在准备本次任务</h3><p>结果由实际库存计算，不使用示例数据。</p>';showPage('run');await refreshState();
}
function events(){
  document.addEventListener('click',e=>{const link=e.target.closest('[data-go]');if(!link)return;showPage(link.dataset.go);if(link.dataset.anchor)document.getElementById(link.dataset.anchor)?.scrollIntoView?.({block:'start',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches?'auto':'smooth'});});
  document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.page==='elixir')renderElixirForm();if(b.dataset.page==='dust')dustUI.form();showPage(b.dataset.page);}));
  $('elixir-target').onchange=e=>{active=draft.demands.findIndex(d=>d.id===e.target.value);renderDraft();persist();};
  $('elixir-goal').oninput=e=>{profile().resource_target_score=e.target.value===''?null:e.target.valueAsNumber;persist();};
  $('elixir-compare').onchange=e=>{elixirOptions().compare_dust=e.target.checked;persist();};
  $('elixir-budget').oninput=e=>{elixirOptions().budget=e.target.valueAsNumber;persist();};
  $('elixir-minimum').oninput=e=>{elixirOptions().minimum_gain=e.target.valueAsNumber;persist();};
  $('elixir-quota').onchange=e=>{elixirOptions().remaining_by_set[profile().set_key]=Number(e.target.value);persist();};
  $('elixir-objective').onchange=e=>{elixirOptions().objective=e.target.value;persist();if(lastElixir)renderElixirResult(lastElixir);};
  $('elixir-priority').onchange=e=>{elixirOptions().respect_priority=e.target.checked;persist();};
  $('elixir-snapshot').onchange=e=>{$('snapshot').value=e.target.value;snapshotNote();renderElixirForm();persist();};
  $('elixir-edit').onclick=()=>showPage('configure');
  $('elixir-save').onclick=protect(async()=>{if(!validateForm($('elixir-form')))throw Error('请填写有效的定制参数');await saveCurrent();notify('定制设置已保存到本地数据库');});
  $('elixir-calculate').onclick=protect(launchElixir);
  $('elixir-export').onclick=()=>lastElixir&&download(lastElixir,'祝圣之霜建议-'+lastElixir.character);
  $('elixir-stop').onclick=protect(async()=>{if(activeElixirJob)await api('/api/stop',{job_id:activeElixirJob,kind:'elixir'});await refreshState();});
  $('mode-single').onclick=()=>{draft.mode='single';changed();renderDraft();};$('mode-multi').onclick=()=>{draft.mode='multi';changed();renderDraft();};
  $('config-name').oninput=e=>{draft.name=e.target.value;changed();};
  $('character').oninput=e=>{profile().character=e.target.value;profile().character_aliases=[e.target.value];$('aliases').value='';$('editor-title').textContent=e.target.value;changed();};
  $('profile-name').oninput=e=>{profile().name=e.target.value;changed();};$('aliases').oninput=e=>{profile().character_aliases=[profile().character,...e.target.value.split(/[,，、]/).map(x=>x.trim()).filter(Boolean)];changed();};
  $('set-key').onchange=e=>{profile().set_key=e.target.value;profile().set_label=catalog.sets[e.target.value];changed();};
  $('threshold').oninput=e=>{profile().threshold=e.target.valueAsNumber/100;changed();};$('crit-cap').oninput=e=>{profile().artifact_crit_rate_cap=e.target.valueAsNumber;changed();};
  $('weights').oninput=e=>{if(e.target.dataset.weight){profile().weights[e.target.dataset.weight]=e.target.valueAsNumber;changed();}};
  $('main-stats').onclick=e=>{const b=e.target.closest('[data-stat]');if(!b)return;const values=profile().main_stats[b.dataset.slot],i=values.indexOf(b.dataset.stat);if(i<0)values.push(b.dataset.stat);else values.splice(i,1);changed();renderEditor();};
  $('equipment').onchange=e=>{draft.equipment=e.target.value;changed();};$('allocation').onchange=e=>{draft.allocation=e.target.value;changed();};
  $('template-select').onchange=e=>{const id=e.target.value;const custom=id.startsWith('user:');const template=(custom?(catalog.presets||[]):catalog.profiles).find(t=>t.id===(custom?id.slice(5):id));if(template){selectedPreset=custom?template.id:null;draft.demands[active].profile=copy(template.data);changed();renderDraft();$('delete-preset').disabled=!selectedPreset;$('preset-note').textContent=custom?'已套用本地预设；修改后保存会更新同角色、同用途的预设。':'内置示例仅作起点，请核对当前养成下的权重与暴击率上限。';}e.target.value='';};
  $('save-preset').onclick=protect(async()=>{if(!validateForm($('profile-form')))throw Error('请先补全角色评分');const r=await api('/api/presets',{action:'save',profile:profile()});selectedPreset=r.id;catalog=await api('/api/catalog');renderPresetOptions();$('delete-preset').disabled=false;notify('角色预设已保存到本地数据库');});
  $('delete-preset').onclick=protect(async()=>{if(!selectedPreset||!window.confirm('删除所选自定义预设？当前培养配置保留。'))return;await api('/api/presets',{action:'delete',id:selectedPreset});selectedPreset=null;catalog=await api('/api/catalog');renderPresetOptions();$('delete-preset').disabled=true;notify('预设已删除');});
  $('add-demand').onclick=()=>{if(draft.demands.length>=40)return notify('最多40个需求',true);draft.demands.push(makeDemand(profile()));active=draft.demands.length-1;profile().name+=' · 新用途';if(draft.scenarios)draft.scenarios[0]?.demands.push(draft.demands[active].id);changed();renderDraft();};
  $('demand-list').onclick=e=>{const card=e.target.closest('[data-demand]');if(!card)return;const i=draft.demands.findIndex(d=>d.id===card.dataset.demand);if(e.target.dataset.move){active=moveDemand(draft,i,i+Number(e.target.dataset.move));changed();}else if(e.target.dataset.remove){if(draft.demands.length===1)return;const [removed]=draft.demands.splice(i,1);draft.scenarios?.forEach(s=>s.demands=s.demands.filter(id=>id!==removed.id));active=Math.min(i,draft.demands.length-1);changed();}else active=i;renderDraft();};
  $('demand-list').onkeydown=e=>{if(e.key==='Enter'&&e.target.dataset.demand)e.target.click();};
  $('demand-list').ondragstart=e=>{dragId=e.target.closest('[data-demand]')?.dataset.demand;};$('demand-list').ondragover=e=>e.preventDefault();
  $('demand-list').ondrop=e=>{e.preventDefault();const target=e.target.closest('[data-demand]')?.dataset.demand;if(!target||!dragId)return;active=moveDemand(draft,draft.demands.findIndex(d=>d.id===dragId),draft.demands.findIndex(d=>d.id===target));changed();renderDraft();};
  $('custom-scenes').onchange=e=>{draft.scenarios=e.target.checked?[{name:'全部同时备齐',demands:draft.demands.map(d=>d.id)}]:null;changed();renderScenes();};
  $('add-scene').onclick=()=>{draft.scenarios.push({name:'场景 '+(draft.scenarios.length+1),demands:[]});changed();renderScenes();};
  $('scenes').oninput=e=>{if(e.target.dataset.sceneName!==undefined){draft.scenarios[+e.target.dataset.sceneName].name=e.target.value;changed();}};
  $('scenes').onchange=e=>{if(e.target.dataset.scene===undefined)return;const s=draft.scenarios[+e.target.dataset.scene],id=e.target.dataset.id;if(e.target.checked)s.demands.push(id);else s.demands=s.demands.filter(x=>x!==id);changed();};
  $('scenes').onclick=e=>{if(e.target.dataset.removeScene!==undefined){draft.scenarios.splice(+e.target.dataset.removeScene,1);changed();renderScenes();}};
  $('snapshot').onchange=()=>{snapshotNote();renderElixirForm();inventoryPanel?.render();persist();};$('fresh-scan').onchange=persist;
  $('auto-equip-after').onchange=e=>{draft.auto_equip_after=e.target.checked;changed();renderScenes();};
  $('equip-scene').onchange=e=>{draft.equip_scene=e.target.value===''?null:Number(e.target.value);changed();};
  $('save-config').onclick=protect(async()=>{await saveCurrent();catalog=await api('/api/catalog');renderCatalog();notify('配置已保存');});
  $('save-as').onclick=protect(async()=>{configId=null;await saveCurrent();persist();catalog=await api('/api/catalog');renderCatalog();notify('已另存为新配置');});
  $('restore-draft').onclick=protect(async()=>{const saved=await api('/api/draft/latest');if(!saved)throw Error('数据库中暂无草稿');draft=saved.payload.config;active=saved.payload.active_index||0;configId=saved.payload.config_id||null;renderDraft();persist();notify('已恢复数据库草稿');});
  $('saved-configs').onchange=protect(async e=>{if(!e.target.value)return;configId=e.target.value;draft=await api('/api/config/'+configId);active=0;renderDraft();persist();$('save-state').textContent='已打开保存的配置';});
  $('export-config').onclick=()=>download(serializeDraft(draft,active),draft.name);
  $('import-config').onclick=()=>$('import-file').click();$('import-file').onchange=protect(async e=>{const file=e.target.files[0];if(!file)return;const data=importDraft(JSON.parse(await file.text()));const saved=await api('/api/config',{config:data});draft=saved.config;configId=saved.id;active=0;renderDraft();persist();catalog=await api('/api/catalog');renderCatalog();notify('配置已导入');e.target.value='';});
  for(const [id,kind] of [['check-backend','backend-check'],['preview','preview'],['start','start'],['resume','resume'],['equip-loadouts','equip']])$(id).onclick=protect(()=>launch(kind));
  $('emergency-stop').onclick=protect(async()=>{await api('/api/emergency-stop',{});notify('已请求紧急中断；保留未确认记录，不自动重试');await refreshState();});
  $('stop').onclick=protect(async()=>{await api('/api/stop',{});notify('已请求停止，正在保留结果');});
  $('save-loadouts').onclick=protect(async()=>{await api('/api/save-loadouts',{});catalog=await api('/api/catalog');renderCatalog();notify('五件配装已保存到本地库');});
  $('download-result').onclick=()=>latestResult&&download(latestResult,'圣遗物配装结果');
  $('refresh-library').onclick=protect(async()=>{catalog=await api('/api/catalog');renderCatalog();});
}
export async function boot(){
  const data=await api('/api/bootstrap');token=data.token;catalog=data.catalog;storageInstance=data.instance||'default';
  const fallback=catalog.profiles.find(p=>p.id==='奥黛塔')||catalog.profiles[0];if(!fallback)throw Error('没有可用的初始角色配置');
  draft={version:1,name:'我的圣遗物培养计划',mode:'single',equipment:'borrow',allocation:'priority',demands:[makeDemand(fallback.data)],scenarios:null};
  let localContext=null;
  try{draftClient=localStorage.getItem(cacheKey('client'));if(!draftClient){draftClient='browser-'+Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem(cacheKey('client'),draftClient);}const stored=JSON.parse(localStorage.getItem(cacheKey('draft')));if(stored?.demands?.length&&stored.mode&&stored.demands.every(d=>d.profile?.main_stats&&d.profile?.weights))draft=stored;localContext=JSON.parse(localStorage.getItem(cacheKey('context')));if(localContext){active=localContext.active_index||0;configId=localContext.config_id||null;contextTime=localContext.updated||0;}}catch{draftClient='session-'+Date.now();}
  // Legacy local drafts have no timestamp; preserve them instead of guessing.
  if(data.draft&&(!localStorage.getItem(cacheKey('draft'))||(contextTime&&data.draft.updated*1000>contextTime))){draft=data.draft.payload.config;active=data.draft.payload.active_index||0;configId=data.draft.payload.config_id||null;localContext=data.draft.payload;}
  $('set-key').innerHTML=Object.entries(catalog.sets).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('');
  renderPresetOptions();
  renderForecast=forecastForm('elixir',elixirOptions,persist);
  renderDraft();renderCatalog();if(localContext?.ui){$('fresh-scan').checked=localContext.ui.fresh!==false;if(catalog.snapshots.some(s=>s.id===localContext.ui.snapshot_id))$('snapshot').value=localContext.ui.snapshot_id;snapshotNote();}renderElixirForm();events();
  dustUI=createDustUI({$,STAT,SLOT,api,notify,download,showPage,persist,validateForm,save:saveCurrent,refresh:refreshState,draft:()=>draft,catalog:()=>catalog,selected:()=>draft.demands[active],select:id=>{active=draft.demands.findIndex(d=>d.id===id);renderDraft();persist();},snapshot:id=>{$('snapshot').value=id;snapshotNote();persist();}});
  await dustUI.restore();
  const previousElixir=await api('/api/elixir/latest');if(previousElixir){renderElixirResult(previousElixir);$('elixir-status').textContent='已恢复上次保存的定制建议。';}
  storagePanel=storageUI({api,notify,download,changed:async removed=>{
    catalog=await api('/api/catalog');
    if(removed.includes(configId)){
      clearTimeout(autosaveTimer);
      if(catalog.configs.length){configId=catalog.configs[0].id;draft=await api('/api/config/'+configId);}
      else {configId=null;draft={version:1,name:'新的培养计划',mode:'single',equipment:'borrow',allocation:'priority',demands:[makeDemand(fallback.data)],scenarios:null};}
      active=0;renderDraft();persist();await flushDraft();
    }
    renderCatalog();
  }});
  inventoryPanel=createInventoryUI({api,catalog:()=>catalog,selected:()=>$('snapshot').value,choose:id=>{$('snapshot').value=id;$('fresh-scan').checked=false;snapshotNote();renderElixirForm();persist();},refresh:async()=>{catalog=await api('/api/catalog');renderCatalog();},scan:()=>launch('scan'),notify});
  inventoryPanel.render();await refreshState();await flushDraft();showPage(window.location.hash?window.location.hash.slice(1):'inventory');
  window.ArtifactWorkbench={getDraft:()=>copy(draft),serialize:()=>serializeDraft(draft,active),showPage,flushDraft,importInventory:file=>inventoryPanel.previewFile(file),
    dispose:()=>{clearTimeout(autosaveTimer);clearTimeout(toastTimer);clearInterval(window.__pollTimer);}};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(autosaveTimer);flushDraft();}});
  window.__pollTimer=setInterval(refreshState,1600);
}
if(typeof document!=='undefined')window.__appReady=boot().catch(e=>{notify(e.message,true);throw e;});

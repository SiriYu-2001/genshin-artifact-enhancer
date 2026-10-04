import {forecastForm,forecastHTML} from './longterm.js';
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modes={efficiency:'每尘收益优先',expected_gain:'平均提升优先',probability:'成功概率优先',target_probability:'达标概率优先',target_gain:'目标内收益优先'};
const title={2:'普通重塑',3:'高阶重塑',4:'谕告重塑'};
const range=(a,b,d=3,m=1)=>`${(a*m).toFixed(d)}–${(b*m).toFixed(d)}`;
export function dustResultHTML(r,objective,STAT,SLOT){
  const rows=r.rankings?.[objective]||[];
  const cards=Object.entries(modes).map(([k,label])=>{const a=r.rankings?.[k]?.[0];if(!a)return '';
    return `<article class="panel elixir-card ${k===objective?'is-selected':''}" data-objective="${k}"><span class="tag">${label}</span><h3>${esc(a.name)} · ${SLOT[a.slot]}</h3><p>${a.selected.map(s=>esc(STAT[s]||s)).join(' ＋ ')}</p><div class="score">${k==='target_probability'?range(a.target_probability_lower,a.target_probability_upper,1,100)+'%':k==='target_gain'?range(a.target_gain_lower,a.target_gain_upper)+' 分':k==='probability'?range(a.probability_lower,a.probability_upper,1,100)+'%':k==='efficiency'?range(a.expected_gain_lower/a.cost,a.expected_gain_upper/a.cost)+' 分/尘':range(a.expected_gain_lower,a.expected_gain_upper)+' 分'}</div><p>${a.cost} 个尘 · 本次${title[a.next_state.guarantee]}（至少${a.next_state.guarantee}次）</p><p>${a.initial_exact?'已确定初始底子':'初值存在不确定性，按保守下界排序'}</p></article>`;}).join('');
  return `<div class="panel"><h2>${esc(r.character)} · ${esc(r.set_label)}</h2><p class="small muted">${esc(r.demand_name)}${r.snapshot?` · 库存 ${esc(r.snapshot.date)}`:''}</p><div class="elixir-meta"><span>基准 ${range(...r.baseline)} 分</span><span>预算 ${r.options.budget} 个尘</span><span>目标 ${r.target_score??'未设置'} 分 · 进度 ${r.options.points}/6</span><span>本轮已完成 ${r.options.phase} 次高阶</span><span>保留优先级装备 ${r.reserved_count} 件</span></div><p>候选 ${r.candidate_count} 件 · 待补录 ${r.deferred_count} 件 · 暴击计分上限 ${r.crit_cap}%</p>${r.message?`<p class="warning">${esc(r.message)}</p>`:''}</div><div class="elixir-cards">${cards}</div>${forecastHTML(r.longterm,STAT,SLOT,"尘")}<details class="panel comparison-panel"><summary>查看候选明细 · ${modes[objective]}</summary><div class="table-wrap"><table><thead><tr><th>圣遗物与所选副词条</th><th>成本／本次保底</th><th>整套提升概率</th><th>平均提升</th><th>若为三次保底</th><th>若为四次保底</th><th>底子</th><th>达标概率 / 目标内收益</th></tr></thead><tbody>${rows.map(a=>`<tr><td>${esc(a.name)} · ${SLOT[a.slot]}<br><small>${esc(a.set_label)} · ${a.selected.map(s=>esc(STAT[s]||s)).join('＋')}</small></td><td>${a.cost}尘／${a.next_state.guarantee}次</td><td>${range(a.probability_lower,a.probability_upper,2,100)}%</td><td>${range(a.expected_gain_lower,a.expected_gain_upper,4)}</td><td>${range(a.guarantees['3'].expected_gain_lower,a.guarantees['3'].expected_gain_upper,4)}</td><td>${range(a.guarantees['4'].expected_gain_lower,a.guarantees['4'].expected_gain_upper,4)}</td><td>${a.upgrade_rolls_possible.join('或')}次随机强化<br><small>${a.initial_exact?'初值已确定':'初值未知，区间结果'}</small></td><td>${a.target_gain_lower==null?'未设置目标':range(a.target_probability_lower,a.target_probability_upper,1,100)+'%<br>'+range(a.target_gain_lower,a.target_gain_upper)+' 分'}</td></tr>`).join('')}</tbody></table></div></details><details class="panel note-panel"><summary>计算范围与结果说明</summary><p>${esc(r.scope)}</p><p>区间重叠时不能认定排名严格优于其他选择。下方可补录初始值和定制锁定词条；重塑后需更新库存和全局进度，再重算建议。失败或选择保留原结果也会消耗尘并推进进度。</p></details>${r.deferred?.length?`<details class="panel"><summary>需要核验的 ${r.deferred.length} 件</summary>${r.deferred.map(x=>`<p>${esc(x.name)}：${esc(x.reason)}</p>`).join('')}</details>`:''}`;
}

export function createDustUI(h){
  const $=h.$;let result=null,job=null;
  const opts=()=>h.draft().dust??=({budget:0,points:0,phase:0,objective:'efficiency',respect_priority:true,metadata:{}});
  const renderForecast=forecastForm('dust',opts,h.persist);
  const safe=fn=>async(...a)=>{try{await fn(...a);}catch(e){h.notify(e.message,true);}};
  function form(){
    renderForecast();
    const d=h.draft(),o=opts(),selected=h.selected();
    $('dust-target').innerHTML=(d.mode==='single'?[selected]:d.demands).map(x=>`<option value="${esc(x.id)}">${esc(x.profile.character)} · ${esc(x.profile.name)}</option>`).join('');$('dust-target').value=selected.id;
    $('dust-snapshot').innerHTML=$('snapshot').innerHTML;$('dust-snapshot').value=$('snapshot').value;
    $('dust-goal').value=selected.profile.resource_target_score??'';
    const advanced=document.querySelector('[data-advanced=dust]');if(advanced&&(selected.profile.resource_target_score!=null||!o.respect_priority))advanced.open=true;
    for(const k of ['budget','points','phase','objective'])$('dust-'+k).value=o[k];$('dust-priority').checked=o.respect_priority;
    const next=o.phase===2?4:3;
    $('dust-rule').textContent=`当前 ${o.points}/6，本轮已触发 ${o.phase} 次高阶。花羽花1尘，沙杯头花2尘；本次达到6即保底${next}次，否则保底2次。进度为全账号共享，不按角色或物品重置。`;
  }
  function render(r){result=r;$('dust-results').className='';$('dust-results').innerHTML=dustResultHTML(r,opts().objective,h.STAT,h.SLOT);$('dust-export').disabled=false;
    $('dust-metadata').hidden=false;const previous=$('dust-item').value;
    $('dust-item').innerHTML='<option value="">选择要补录的圣遗物…</option>'+r.inventory.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${h.SLOT[a.slot]} · ${a.id.split(':').at(-1)}${a.error?' · 待核验':''}</option>`).join('');
    if(r.inventory.some(a=>a.id===previous))$('dust-item').value=previous;editor();
  }
  function editor(){
    const a=result?.inventory.find(x=>x.id===$('dust-item').value);$('dust-item-editor').hidden=!a;if(!a)return;
    const m=opts().metadata[a.fingerprint]||{};$('dust-rolls').value=m.upgrade_rolls??'';
    $('dust-item-stats').textContent=a.substats.map(s=>`${h.STAT[s.key]} ${s.value}`).join(' · ');
    $('dust-initials').innerHTML=a.substats.map(s=>`<label>${esc(h.STAT[s.key])}初始值<select data-dust-base="${s.key}"><option value="">未知，保留范围</option>${h.catalog().rolls[s.key].map(v=>`<option value="${v}" ${m.initial_values?.[s.key]===v?'selected':''}>${v}${s.key.endsWith('_')?'%':''}</option>`).join('')}</select></label>`).join('');
    $('dust-defined').hidden=a.special==='ordinary';$('dust-defined-pair').innerHTML=a.substats.map(s=>`<label class="check-line"><input type="checkbox" data-dust-pair="${s.key}" ${(m.defined_pair||[]).includes(s.key)?'checked':''}>${esc(h.STAT[s.key])}</label>`).join('');
  }
  $('dust-target').onchange=e=>{h.select(e.target.value);form();};$('dust-snapshot').onchange=e=>{h.snapshot(e.target.value);form();};
  for(const k of ['budget','points','phase','objective'])$('dust-'+k).onchange=e=>{opts()[k]=k==='objective'?e.target.value:Number(e.target.value);h.persist();form();if(result&&k==='objective')render(result);};
  $('dust-goal').oninput=e=>{h.selected().profile.resource_target_score=e.target.value===''?null:e.target.valueAsNumber;h.persist();};
  $('dust-priority').onchange=e=>{opts().respect_priority=e.target.checked;h.persist();};
  $('dust-calculate').onclick=safe(async()=>{
    if(!h.validateForm($('dust-form')))throw Error('请填写有效的预算和进度');
    const config_id=await h.save(),demand_id=h.selected().id;
    await h.api('/api/jobs',{kind:'dust',config_id,demand_id,snapshot_id:$('snapshot').value});
    result=null;$('dust-results').innerHTML='<div class="panel">正在逐件分析固定底子和保底收益…</div>';$('dust-export').disabled=true;$('dust-metadata').hidden=true;h.showPage('dust');await h.refresh();
  });
  $('dust-stop').onclick=safe(async()=>{if(job)await h.api('/api/stop',{kind:'dust',job_id:job});await h.refresh();});
  $('dust-save').onclick=safe(async()=>{if(!h.validateForm($('dust-form')))throw Error('参数无效');await h.save();h.notify('启圣之尘设置已保存');});
  $('dust-export').onclick=()=>result&&h.download(result,'启圣之尘建议-'+result.character);
  $('dust-item').onchange=editor;
  $('dust-meta-save').onclick=safe(async()=>{
    const a=result?.inventory.find(x=>x.id===$('dust-item').value);if(!a)return;
    const initial_values={};document.querySelectorAll('[data-dust-base]').forEach(x=>{if(x.value!=='')initial_values[x.dataset.dustBase]=Number(x.value);});
    const meta={initial_values};if($('dust-rolls').value!=='')meta.upgrade_rolls=Number($('dust-rolls').value);
    if(a.special!=='ordinary'){const pair=[...document.querySelectorAll('[data-dust-pair]:checked')].map(x=>x.dataset.dustPair);if(pair.length!==2)throw Error('请填写游戏锁定的两条副词条');meta.defined_pair=pair;}
    opts().metadata[a.fingerprint]=meta;h.persist();await h.save();h.notify('底子信息已保存，请重新计算以验证合法性和更新建议');
  });
  $('dust-meta-clear').onclick=safe(async()=>{const a=result?.inventory.find(x=>x.id===$('dust-item').value);if(a){delete opts().metadata[a.fingerprint];h.persist();await h.save();editor();}});
  return {form,render,state(s){$('dust-calculate').disabled=s.busy;job=s.busy&&s.job?.kind==='dust'?s.job.id:null;$('dust-stop').hidden=!job;if(s.job?.kind!=='dust')return;
    const j=s.job;let message='正在读取库存…';for(const line of [...j.logs].reverse()){try{const x=JSON.parse(line);if(x.phase==='longterm'){message=`长期对照：已模拟 ${x.done}/${x.total} 条未来路径`;break;}if(x.phase==='dust'){message=`${x.character}：已分析 ${x.done}/${x.total} 件`;break;}}catch{}}
    $('dust-status').textContent=j.status==='running'?message:j.status==='completed'?'建议已计算并保存；实际重塑由你手动决定。':j.status==='stopped'?'计算已停止，设置已保留。':j.result?.error||'计算失败';
  },async restore(){const old=await h.api('/api/dust/latest');if(old)render(old);form();}};
}

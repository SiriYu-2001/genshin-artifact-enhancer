const escapeHTML=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bytes=n=>n>=1073741824?(n/1073741824).toFixed(2)+' GiB':n>=1048576?(n/1048576).toFixed(1)+' MiB':(n/1024).toFixed(1)+' KiB';

export function storageUI({api,notify,download,changed}){
  const $=id=>document.getElementById(id);let state=null,preview=null;
  const run=fn=>async()=>{try{await fn();}catch(e){notify(e.message,true);}};
  function table(rows,trash=false){
    if(!rows.length)return '<p class="muted">暂无配置</p>';
    return `<div class="table-wrap"><table><thead><tr><th>${trash?'':'选择'}</th><th>配置</th><th>保存时间</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${trash?'':`<input type="checkbox" data-config-select="${r.id}" aria-label="选择 ${escapeHTML(r.config.name)}">`}</td><td><b>${escapeHTML(r.config.name)}</b><br><small>${r.config.demands.length} 个需求 · ${r.id.slice(0,8)}<br>${escapeHTML(r.config.demands.map(d=>d.profile.character).join('、'))}</small></td><td>${escapeHTML(new Date((r.updated||r.deleted)*1000).toLocaleString())}</td><td><button class="btn ghost" data-export="${r.id}">导出</button>${trash?`<button class="btn" data-restore="${r.id}">恢复</button><button class="btn ghost" data-purge="${r.id}">彻底删除</button>`:`<button class="btn ghost" data-archive="${r.id}">删除</button>`}</td></tr>`).join('')}</tbody></table></div>`;
  }
  async function refresh(){
    state=await api('/api/storage');preview=null;$('storage-clean').disabled=true;
    $('storage-path').textContent=state.directory;
    $('storage-size').textContent=`数据库 ${bytes(state.sizes.database||0)} · 截图 ${bytes(state.sizes.screenshots||0)} · 文本及结果 ${bytes(state.sizes.records||0)}`;
    $('storage-configs').innerHTML=table(state.configs);$('storage-trash').innerHTML=table(state.trash,true);
    $('storage-trash-count').textContent=state.trash.length;
    $('storage-preview-result').textContent=state.blocked.length?'清理暂停：'+state.blocked.join('；'):`可清理 ${state.count} 张正常截图，约 ${bytes(state.bytes)}；保留 ${state.protected_screenshots} 张错误证据。`;
  }
  async function archive(ids){
    if(!ids.length)return notify('请先选择配置');
    if(!window.confirm(`将选中的 ${ids.length} 份配置移入回收站？可恢复；不会删除库存、配装或运行记录。`))return;
    for(const id of ids)await api('/api/storage',{action:'archive',id});
    await changed(ids);await refresh();notify('配置已移入回收站');
  }
  $('storage-refresh').onclick=run(refresh);
  $('storage-delete-selected').onclick=run(()=>archive([...document.querySelectorAll('[data-config-select]:checked')].map(e=>e.dataset.configSelect)));
  $('page-storage').addEventListener('click',async event=>{
    const b=event.target.closest('button');if(!b)return;
    try{
      if(b.dataset.archive)await archive([b.dataset.archive]);
      if(b.dataset.export){const r=[...state.configs,...state.trash].find(r=>r.id===b.dataset.export);download(r.config,r.config.name);}
      if(b.dataset.restore){await api('/api/storage',{action:'restore',id:b.dataset.restore});await changed([]);await refresh();notify('配置已恢复');}
      if(b.dataset.purge&&window.confirm('彻底删除这份配置和它的历史版本？此操作不可恢复，需备份时请先导出。')){
        await api('/api/storage',{action:'purge',id:b.dataset.purge,confirm:b.dataset.purge});await refresh();notify('配置已彻底删除');
      }
    }catch(e){notify(e.message,true);}
  });
  $('storage-preview').onclick=run(async()=>{
    preview=await api('/api/storage',{action:'preview'});
    $('storage-preview-result').textContent=preview.blocked.length?'清理暂停：'+preview.blocked.join('；'):`将删除 ${preview.count} 张正常截图，释放 ${bytes(preview.bytes)}。错误证据、库存、配装及文本回执保留。`;
    $('storage-clean').disabled=!!preview.blocked.length||!preview.count;
  });
  $('storage-clean').onclick=run(async()=>{
    if(!preview||!window.confirm(`确认清理预览中的 ${preview.count} 张正常截图（${bytes(preview.bytes)}）？`))return;
    const result=await api('/api/storage',{action:'clean',preview:preview.preview});await refresh();notify(`已释放 ${bytes(result.bytes)}`);
  });
  $('storage-compact').onclick=run(async()=>{
    if(!window.confirm('将每份配置和草稿的历史缩减至最近20版，并压缩数据库？当前配置不会删除。'))return;
    await api('/api/storage',{action:'compact'});await refresh();notify('历史已整理，数据库已压缩');
  });
  return {refresh:run(refresh)};
}

const escapeHTML=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function createInventoryUI({api,catalog,selected,choose,refresh,scan,notify}){
  const $=id=>document.getElementById(id);let preview=null,working=false,jobBusy=false;
  const run=fn=>async(...args)=>{try{await fn(...args);}catch(e){notify(e.message,true);$('inventory-message').textContent=e.message;}finally{working=false;buttons();}};
  function buttons(){
    $('inventory-file').disabled=working||jobBusy;$('inventory-scan').disabled=working||jobBusy;
    $('inventory-import').disabled=working||jobBusy;$('inventory-confirm').disabled=working||jobBusy||!preview?.can_import;
  }
  function render(){
    const entries=catalog().snapshots;
    $('inventory-snapshot').innerHTML=entries.length?entries.map(s=>`<option value="${s.id}">${escapeHTML(s.date)} · ${s.five_star} 件五星 · ${escapeHTML(s.source==='import'?s.label:'游戏扫描')}</option>`).join(''):'<option value="">还没有库存</option>';
    $('inventory-snapshot').value=selected();
    const s=entries.find(x=>x.id===selected());
    $('inventory-next').disabled=!s;
    $('inventory-current').textContent=s?`已选择 ${s.five_star} 件五星${s.source==='import'?' · 仅计算文件内库存':' · 游戏扫描记录'}${s.ownership_stale?' · 装备归属可能已变化':''}`:'先导入一个 JSON，或选择扫描游戏背包。';
  }
  async function fileSelected(file){
    if(!file)return;if(jobBusy)throw Error('请等待当前任务结束后再导入');
    if(file.size>10*1024*1024)throw Error('JSON 文件最大 10 MiB');
    working=true;preview=null;buttons();$('inventory-message').textContent='正在检查文件内容…';
    const content=await file.text();preview=await api('/api/inventory/import',{action:'preview',filename:file.name,content});
    const p=preview;
    $('inventory-preview').hidden=false;
    $('inventory-preview-body').innerHTML=`<h3>${escapeHTML(file.name)}</h3><p>${escapeHTML(p.format)} · 文件 ${p.total} 件 · 接收 ${p.accepted} 件五星 · 跳过 ${p.skipped_non_five} 件低星</p><p>满级 ${p.mature} 件 · 定制 ${p.defined} 件 · 缺少定制状态 ${p.unknown_kind} 件 · 缺少第四词条 ${p.missing_preview} 件</p><p class="small muted">装备归属未知 ${p.unknown_owner} 件；莫娜标记忽略 ${p.excluded} 件；相同属性的额外物品 ${p.identical_extra} 件（分别保留）。</p>${p.error_count?`<p class="warning">${p.error_count} 件数据需要修正，尚未写入库存。</p><ul>${p.errors.map(e=>`<li>${escapeHTML(e.item)}：${escapeHTML(e.reason)}</li>`).join('')}</ul>`:''}<p class="small muted">${escapeHTML(p.scope)}</p>`;
    $('inventory-message').textContent=p.can_import?'检查完成，确认后保存到本地库存。':'请修正文件中的错误后重新导入。';
    working=false;buttons();
  }
  $('inventory-import').onclick=()=>$('inventory-file').click();
  $('inventory-file').onchange=run(async e=>{const file=e.target.files[0];e.target.value='';await fileSelected(file);});
  const drop=$('inventory-drop');drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag-over');};
  drop.ondragleave=()=>drop.classList.remove('drag-over');
  drop.ondrop=run(async e=>{e.preventDefault();drop.classList.remove('drag-over');await fileSelected(e.dataTransfer.files[0]);});
  $('inventory-confirm').onclick=run(async()=>{
    working=true;buttons();const result=await api('/api/inventory/import',{action:'commit',preview:preview.preview});
    await refresh();choose(result.snapshot_id);preview=null;render();$('inventory-preview').hidden=true;
    $('inventory-message').textContent=result.duplicate?'这份库存已经导入，已选中原有记录。':'库存已保存。下一步选择角色预设，或编辑评分和主属性。';
    notify(result.duplicate?'已选择已有库存':'库存导入成功');
  });
  $('inventory-snapshot').onchange=e=>{choose(e.target.value);render();};
  $('inventory-scan').onclick=run(scan);
  return {render,state:busy=>{jobBusy=busy;buttons();},previewFile:run(fileSelected)};
}

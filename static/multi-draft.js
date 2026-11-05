(() => {
  let drafts = [], duplicateNotice = [];
  const data = () => window.getWmtDraftData?.() || {users:[],nodes:{}};
  const byId = id => data().users.find(x => Number(x.id) === Number(id));
  const esc = value => window.esc ? window.esc(String(value ?? '')) : String(value ?? '');
  function render() {
    const root=document.querySelector('#draftResult'); if(!root) return;
    const groups={}; drafts.forEach(d=>{const key=d.slots.owner_id||'none';(groups[key]??=[]).push(d)});
    root.innerHTML='<h3>任务草稿</h3><p class="muted">按责任人分组，可逐条展开并编辑。</p>';
    if(duplicateNotice.length){
      const exact=duplicateNotice.filter(x=>x.kind==='exact'), similar=duplicateNotice.filter(x=>x.kind==='similar');
      const names=items=>items.slice(0,3).map(x=>'《'+(x.title||'未命名任务')+'》').join('、');
      const notice=document.createElement('div'); notice.className='draft-duplicate-notice';
      notice.innerHTML=(exact.length?'<strong>已阻止 '+exact.length+' 条完全重复任务：</strong>'+esc(names(exact))+'。':'')+(similar.length?'<br><strong>发现 '+similar.length+' 条相似任务：</strong>'+esc(names(similar))+'。请核对后再创建。':'');
      root.append(notice);
    }
    Object.entries(groups).forEach(([owner,items])=>{
      const group=document.createElement('details'); group.open=true; group.className='multi-owner-group';
      const summary=document.createElement('summary'); summary.textContent=(owner==='none'?'未分配':(byId(owner)?.name||'未知责任人'))+' · '+items.length+' 条任务'; group.append(summary);
      items.forEach(d=>group.append(card(d))); root.append(group);
    });
    if(drafts.length){const button=document.createElement('button');button.textContent='创建已选有效任务';button.onclick=createSelected;root.append(button)}
  }
  function card(d) {
    const s=d.slots, box=document.createElement('details'); box.className='multi-task-card';
    const summary=document.createElement('summary'); summary.textContent=(s.title||'待明确任务')+' · '+(s.due_at?String(s.due_at).slice(0,10):'待补充日期'); box.append(summary);
    if(d.similar_tasks?.length){const warning=document.createElement('p');warning.className='draft-card-warning';warning.textContent='发现相似任务：'+d.similar_tasks.map(x=>'《'+x.title+'》').join('、')+'；请核对后再创建。';box.append(warning)}
    const form=document.createElement('form'); form.innerHTML='<label>任务标题<input name="title" required maxlength="40"></label><label>责任人<select name="owner_id" required></select></label><label>责任域<select name="org_node_id"></select></label><label>截止日期<input name="due_at" type="date" required></label><label>优先级<select name="priority"></select></label><label>任务类型<select name="task_type"></select></label><label>任务详情<textarea name="description"></textarea></label><button>保存草稿</button><button type="button" class="secondary">删除</button>';
    form.title.value=s.title||''; form.due_at.value=String(s.due_at||'').slice(0,10); form.priority.value=s.priority||''; form.task_type.value=s.task_type||''; form.description.value=s.description||'';
    const placeholder=(select,label,selected)=>{const option=new Option(label,'',selected,selected);option.disabled=true;option.hidden=true;select.add(option)};
    placeholder(form.owner_id,'请选择',!s.owner_id); placeholder(form.org_node_id,'待补充',!s.org_node_id);
    placeholder(form.priority,'待补充',!s.priority); placeholder(form.task_type,'待补充',!s.task_type);
    data().users.forEach(u=>{const o=new Option(u.name,u.id,Number(u.id)===Number(s.owner_id),Number(u.id)===Number(s.owner_id));form.owner_id.add(o)});
    Object.entries(data().nodes).forEach(([id,name])=>{const o=new Option(name,id,Number(id)===Number(s.org_node_id),Number(id)===Number(s.org_node_id));form.org_node_id.add(o)});
    ['P0','P1','P2'].forEach(x=>form.priority.add(new Option(x,x,x===s.priority,x===s.priority)));
    [['PROJECT','专项任务'],['ROUTINE','例行任务'],['ADHOC','临时任务']].forEach(([value,label])=>form.task_type.add(new Option(label,value,value===s.task_type,value===s.task_type)));
    [form.owner_id,form.org_node_id,form.priority,form.task_type].forEach(select=>{const sync=()=>select.classList.toggle('is-empty',!select.value);select.addEventListener('change',sync);sync()});
    form.onsubmit=async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(form));data.owner_id=Number(data.owner_id);data.org_node_id=data.org_node_id?Number(data.org_node_id):null;data.due_at=data.due_at+'T17:00:00+08:00';const r=await post('/api/task/draft/'+d.id+'/complete',data),out=await r.json();if(!r.ok)return alert(out.error||'保存失败');Object.assign(d,out);render()};
    form.querySelector('[type="button"]').onclick=async()=>{await post('/api/task/draft/'+d.id+'/discard',{});drafts=drafts.filter(x=>x.id!==d.id);render()};
    box.append(form); return box;
  }
  async function createSelected(){
    const selected=drafts.filter(x=>!x.missing_slots?.length);
    if(selected.some(x=>x.similar_tasks?.length) && !confirm('存在相似任务，仍要创建选中的草稿吗？')) return;
    let count=0; const failed=[], createdIds=[];
    for(const d of selected){const r=await post('/api/task/draft/'+d.id+'/confirm',{}),out=await r.json();if(r.ok){count++;createdIds.push(d.id)}else failed.push(out.error||'创建失败')}
    drafts=drafts.filter(x=>!createdIds.includes(x.id)); render();
    alert(failed.length?('已创建 '+count+' 条；另有 '+failed.length+' 条未创建：'+failed[0]):('已创建 '+count+' 条待上级确认任务'));
    if(count) window.load?.();
  }
  window.createDraft=async()=>{const text=document.querySelector('#draftText')?.value.trim(),button=document.querySelector('button[onclick="createDraft()"]');if(!text)return alert('请输入会议纪要或任务材料');const originalLabel=button?.textContent;if(button){button.disabled=true;button.textContent='内容生成中···'}try{const r=await post('/api/task/drafts/parse',{text,user_id:window.currentActorId?.()}),out=await r.json();if(!r.ok)throw Error(out.error||'草稿生成失败');drafts=out.drafts||[];duplicateNotice=out.duplicates||[];const input=document.querySelector('#draftText'),files=document.querySelector('#draftFiles'),fileStatus=document.querySelector('#draftFileStatus');if(input)input.value='';if(files)files.value='';if(fileStatus)fileStatus.textContent='';render();if(!drafts.length&&duplicateNotice.some(x=>x.kind==='exact'))alert('未生成新草稿：检测到完全重复的任务。')}catch(error){alert(error.message||'草稿生成失败，请重试')}finally{if(button){button.disabled=false;button.textContent=originalLabel||'生成任务草稿'}}};
  function unifyCreateArea() {
    const ai=document.querySelector('.ai-create-card'), manual=document.querySelector('#form')?.closest('.card');
    if(!ai||!manual||ai.dataset.unified==='1') return;
    ai.dataset.unified='1';
    const title=ai.querySelector('h2'); if(title) title.textContent='创建任务';
    const intro=ai.querySelector('p.muted'); intro?.remove();
    const header=document.createElement('div'); header.className='create-header'; ai.insertBefore(header,title); header.append(title);
    const text=ai.querySelector('#draftText');
    if(text){
      text.placeholder='请输入会议纪要等资料，或点击附件上传提取后识别任务';
      const initialHeight=Math.ceil(text.getBoundingClientRect().height);
      const maxHeight=initialHeight*2;
      const resizeText=()=>{text.style.height='auto';text.style.height=Math.min(Math.max(initialHeight,text.scrollHeight),maxHeight)+'px';text.style.overflowY=text.scrollHeight>maxHeight?'auto':'hidden'};
      text.addEventListener('input',resizeText); requestAnimationFrame(resizeText);
    }
    const uploadRow=ai.querySelector('.draft-upload-row'), upload=uploadRow?.querySelector('.upload-trigger'), picker=uploadRow?.querySelector('#draftFiles'), fileStatus=ai.querySelector('#draftFileStatus');
    const generate=ai.querySelector('button[onclick="createDraft()"]');
    const result=document.querySelector('#draftResult');
    const aiSection=document.createElement('section'); aiSection.className='ai-assist-section';
    const inputWrap=document.createElement('div'); inputWrap.className='ai-input-wrap'; if(text) inputWrap.append(text);
    if(upload){upload.className='draft-attachment-button';upload.innerHTML='&#128206;';upload.title='支持 Word、PDF、图片识别';inputWrap.append(upload)}
    if(picker) inputWrap.append(picker);
    aiSection.innerHTML='<div class="ai-assist-heading"><h3>AI 辅助建单</h3><p>输入会议纪要或上传材料，系统提炼明确待办并生成可确认的任务草稿。</p></div>';
    aiSection.append(inputWrap); if(generate) aiSection.append(generate); if(fileStatus){fileStatus.hidden=true;aiSection.append(fileStatus)} if(result) aiSection.append(result);
    uploadRow?.remove();
    const manualSection=document.createElement('section'); manualSection.className='manual-create-section';
    const form=document.querySelector('#form'); manualSection.append(form);
    const createMessage=document.querySelector('#msg'); if(createMessage) manualSection.append(createMessage);
    // Keep both entry paths visible in one continuous operation panel.  A mode
    // toggle hid useful content and left a large, misleading blank area.
    ai.append(manualSection,aiSection);
    manual.remove();
    const style=document.createElement('style');
    style.textContent='.ai-create-card{max-width:none!important}.create-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.create-header h2{margin:0}.create-mode-toggle{display:flex;border:1px solid #cbd9f0;border-radius:9px;padding:3px;background:#f6f9fe}.create-mode-toggle button{margin:0;padding:7px 14px;background:transparent;color:#60718a;box-shadow:none}.create-mode-toggle button.active{background:#2864d7;color:#fff;border-radius:6px}.ai-input-wrap{display:flex;flex-direction:column;align-items:flex-start}.ai-input-wrap textarea{padding:18px 16px!important}.draft-attachment-button{display:grid;place-items:center;width:30px;height:30px;margin:8px 0 0 4px;padding:0;border:0;border-radius:0;background:transparent;color:#2864d7;font-size:21px;line-height:1;box-shadow:none;cursor:pointer}.draft-attachment-button:hover{background:transparent;color:#174ba9;transform:translateY(-1px)}.ai-input-wrap #draftFiles{display:none}.manual-create-section{margin-top:18px}.multi-draft-results{margin-top:18px}.multi-draft-results>h3{margin:0}.multi-owner-group{margin-top:10px;padding:0;border:0;background:transparent}.multi-owner-group>summary,.multi-task-card>summary{cursor:pointer;font-weight:700;color:#183b6f}.multi-task-card{margin:10px 0;padding:12px;border:1px solid #e0e9f5;border-radius:9px;background:#fff}.multi-task-card>summary{display:flex;justify-content:space-between;gap:12px}.multi-task-card form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.multi-task-card form label:nth-last-of-type(-n+2){grid-column:1/-1}.multi-task-card textarea{min-height:76px}.multi-task-card form div{grid-column:1/-1;display:flex;gap:8px}.multi-task-card select.is-empty{color:#98a5b8}.multi-task-card select option{color:#172033}.draft-generating,.draft-generated,.draft-error{display:inline-block;margin:10px 0 0 12px;font-size:13px;font-weight:600}.draft-generating{color:#2864d7}.draft-generating:after{content:\" ···\";display:inline-block;animation:draft-wave 1s steps(4,end) infinite}@keyframes draft-wave{0%,100%{opacity:.25}50%{opacity:1}}.draft-generated{color:#178049}.draft-error{color:#c83333}@media(max-width:700px){.create-header{align-items:flex-start;flex-direction:column}.multi-task-card form{grid-template-columns:1fr}}';
    style.textContent+='.ai-input-wrap{position:relative;display:block}.ai-input-wrap textarea{box-sizing:border-box;padding:18px 58px 58px 16px!important;resize:none}.draft-attachment-button{position:absolute;right:12px;bottom:12px;display:grid;place-items:center;width:30px;height:30px;margin:0;padding:0;border:0;border-radius:0;background:transparent;color:#2864d7;font-size:21px;line-height:1;box-shadow:none;cursor:pointer}.draft-attachment-button:hover{background:transparent;color:#174ba9;transform:translateY(-1px)}/* Home-column geometry is owned by index.html. This feature stylesheet stays inside the operation panel. */.home-operations>.ai-create-card{height:auto!important;box-sizing:border-box;margin:0;overflow:visible!important}.ai-create-card .manual-create-section{display:block!important;min-height:0;padding:0 4px 6px 0;overflow:visible!important}.ai-create-card .manual-create-section .task-create-form{height:auto!important;overflow:visible!important}.ai-create-card .manual-create-section .task-create-form .form-actions{position:relative!important;display:flex!important;align-items:flex-start!important;min-height:0!important;margin:12px 0 0!important;padding:0!important}.ai-create-card .manual-create-section .task-create-form .form-actions button{position:static!important;margin:0!important}.ai-create-card .manual-create-section #msg:empty{display:none!important}.ai-create-card .manual-create-section #msg{margin:8px 0 0!important}.ai-create-card .ai-assist-section{clear:both;min-height:0;padding:16px 4px 24px 0;margin-top:0!important;border-top:1px solid #e3eaf4}.ai-assist-heading{margin:0 0 12px}.ai-assist-heading h3{margin:0;color:#183b6f;font-size:18px}.ai-assist-heading p{margin:6px 0 0;color:#6d7b90;font-size:13px}@media(max-width:800px){.home-operations>.ai-create-card{height:auto;min-height:0}.ai-create-card .manual-create-section,.ai-create-card .ai-assist-section{overflow:visible}}';
    // Reset legacy broad selectors before applying the scoped operation-panel
    // styles below.  These classes also occur in other views, so they must
    // never be allowed to change a page outside of the home operation card.
    style.textContent=`
      .ai-create-card{max-width:none!important}
      .ai-create-card .create-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 0 16px}.ai-create-card .create-header h2{margin:0}
      .ai-create-card .ai-input-wrap{position:relative;display:block}.ai-create-card .ai-input-wrap textarea{box-sizing:border-box;padding:18px 58px 58px 16px!important;resize:none}
      .ai-create-card .draft-attachment-button{position:absolute;right:12px;bottom:12px;display:grid;place-items:center;width:30px;height:30px;margin:0;padding:0;border:0;background:transparent;color:#2864d7;font-size:21px;line-height:1;box-shadow:none;cursor:pointer}.ai-create-card .draft-attachment-button:hover{color:#174ba9;transform:translateY(-1px)}.ai-create-card .ai-input-wrap #draftFiles{display:none}
      .home-operations>.ai-create-card{height:auto!important;box-sizing:border-box;margin:0;overflow:visible!important}.ai-create-card .manual-create-section{display:block!important;min-height:0;margin:0!important;padding:0 4px 6px 0;overflow:visible!important}.ai-create-card .manual-create-section .task-create-form{height:auto!important;overflow:visible!important}.ai-create-card .manual-create-section .task-create-form .form-actions{position:relative!important;display:flex!important;align-items:flex-start!important;min-height:0!important;margin:12px 0 0!important;padding:0!important}.ai-create-card .manual-create-section .task-create-form .form-actions button{position:static!important;margin:0!important}.ai-create-card .manual-create-section #msg:empty{display:none!important}.ai-create-card .manual-create-section #msg{margin:8px 0 0!important}
      .ai-create-card .ai-assist-section{clear:both;min-height:0;margin-top:0!important;padding:14px 4px 20px 0;border-top:1px solid #e3eaf4}.ai-create-card .ai-assist-heading{margin:0 0 10px}.ai-create-card .ai-assist-heading h3{margin:0;color:#183b6f;font-size:18px}.ai-create-card .ai-assist-heading p{margin:6px 0 0;color:#6d7b90;font-size:13px}
      .ai-create-card .multi-draft-results{margin-top:18px}.ai-create-card .multi-draft-results>h3{margin:0}.ai-create-card .multi-owner-group{margin-top:10px;padding:0;border:0;background:transparent}.ai-create-card .multi-owner-group>summary,.ai-create-card .multi-task-card>summary{cursor:pointer;font-weight:700;color:#183b6f}.ai-create-card .multi-task-card{margin:10px 0;padding:12px;border:1px solid #e0e9f5;border-radius:9px;background:#fff}.ai-create-card .multi-task-card>summary{display:flex;justify-content:space-between;gap:12px}.ai-create-card .multi-task-card form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.ai-create-card .multi-task-card form label:nth-last-of-type(-n+2){grid-column:1/-1}.ai-create-card .multi-task-card textarea{min-height:76px}.ai-create-card .multi-task-card form div{grid-column:1/-1;display:flex;gap:8px}.ai-create-card .multi-task-card select.is-empty{color:#98a5b8}.ai-create-card .multi-task-card select option{color:#172033}.ai-create-card .draft-generating,.ai-create-card .draft-generated,.ai-create-card .draft-error{display:inline-block;margin:10px 0 0 12px;font-size:13px;font-weight:600}.ai-create-card .draft-generating{color:#2864d7}.ai-create-card .draft-generating:after{content:' ···';display:inline-block;animation:draft-wave 1s steps(4,end) infinite}.ai-create-card .draft-generated{color:#178049}.ai-create-card .draft-error{color:#c83333}.ai-create-card .draft-duplicate-notice{margin:12px 0;padding:10px 12px;border:1px solid #f1d08d;border-radius:8px;background:#fff8e8;color:#7b570d;font-size:13px;line-height:1.6}.ai-create-card .draft-card-warning{margin:8px 0 0;color:#a06700;font-size:13px;line-height:1.5}
      @keyframes draft-wave{0%,100%{opacity:.25}50%{opacity:1}}@media(max-width:800px){.home-operations>.ai-create-card{height:auto;min-height:0}.ai-create-card .manual-create-section,.ai-create-card .ai-assist-section{overflow:visible}.ai-create-card .manual-create-section .task-create-form>.core-create-field{flex-basis:100%!important}}@media(max-width:700px){.ai-create-card .create-header{align-items:flex-start;flex-direction:column}.ai-create-card .multi-task-card form{grid-template-columns:1fr}}
    `;
    // Repair the legacy height override above: the tree is a scrollable child,
    // while the card itself must take the exact height of the operation panel.
    style.textContent+='/* Structural guard: feature styles must not turn global navigation into stacked primary buttons. */.workspace-header .header-nav-row{display:flex!important;flex-direction:row!important;align-items:center!important;justify-content:space-between!important;gap:18px!important;min-width:0!important}.workspace-header .nav-tabs{display:flex!important;flex:1 1 auto!important;flex-direction:row!important;flex-wrap:nowrap!important;align-items:center!important;justify-content:flex-start!important;gap:10px!important;margin:0!important;width:auto!important;min-width:0!important;max-width:100%!important;overflow-x:auto!important;overflow-y:hidden!important}.workspace-header .nav-tabs>button{display:inline-flex!important;flex:0 0 auto!important;align-items:center!important;justify-content:center!important;width:auto!important;min-width:auto!important;margin:0!important;padding:5px 14px!important;white-space:nowrap!important;background:transparent!important;color:#dbe8ff!important;border:1px solid transparent!important;border-radius:0!important;box-shadow:none!important}.workspace-header .nav-tabs>button:hover,.workspace-header .nav-tabs>button.active{background:transparent!important;color:#fff!important;border-color:transparent!important;border-bottom:2px solid #79a8ff!important;box-shadow:none!important}.workspace-header .header-nav-row .demo-actor{position:static!important;flex:0 0 auto!important;align-self:auto!important;margin:0!important}@media(max-width:800px){.workspace-header .header-nav-row{gap:10px!important}.workspace-header .nav-tabs{gap:4px!important}.workspace-header .nav-tabs>button{padding:5px 10px!important}}';
    // Keep the two creation paths visually compact and on a single spacing rhythm.
    style.textContent+='.ai-create-card .create-header{margin:0 0 16px!important}.ai-create-card .manual-create-section{margin-top:0!important;padding-bottom:4px!important}.ai-create-card .manual-create-section .task-create-form{display:flex!important;flex-wrap:wrap!important;align-content:flex-start!important;gap:18px 22px!important}.ai-create-card .manual-create-section .task-create-form>.form-field{margin:0!important}.ai-create-card .manual-create-section .task-create-form>.form-field.full,.ai-create-card .manual-create-section .task-create-form>.task-advanced-fields,.ai-create-card .manual-create-section .task-create-form>.form-actions{flex:0 0 100%!important;width:100%!important}.ai-create-card .manual-create-section .task-create-form>.core-create-field{flex:1 1 calc((100% - 44px)/3)!important;min-width:0}.ai-create-card .manual-create-section .task-create-form label{display:block!important;margin:0 0 8px!important;line-height:1.35!important}.ai-create-card .manual-create-section .task-advanced-fields{margin-top:0!important}.ai-create-card .manual-create-section .task-create-form .form-actions{margin-top:0!important}.ai-create-card .ai-assist-section{padding-top:14px!important;padding-bottom:20px!important}.ai-create-card .ai-assist-heading{margin:0 0 10px!important}.ai-create-card .ai-assist-heading p{margin-bottom:0!important}.draft-duplicate-notice{margin:12px 0;padding:10px 12px;border:1px solid #f1d08d;border-radius:8px;background:#fff8e8;color:#7b570d;font-size:13px;line-height:1.6}.draft-card-warning{margin:8px 0 0;color:#a06700;font-size:13px;line-height:1.5}@media(max-width:800px){.ai-create-card .manual-create-section .task-create-form>.core-create-field{flex-basis:100%!important}}';
    document.head.append(style);
    const removeLegacyDeliverable=()=>{
      document.querySelectorAll('label').forEach(label=>{if(!label.querySelector('[name="deliverable"]'))return;const field=label.closest('.form-field,.full');(field||label).remove()});
      document.querySelectorAll('.task-detail-grid dt').forEach(dt=>{if(!/交付物|验收标准/.test(dt.textContent))return;dt.nextElementSibling?.remove();dt.remove()});
      document.querySelectorAll('.task .muted').forEach(node=>{const next=node.textContent.replace(/\s*·\s*验收[：:].*$/,'');if(next!==node.textContent)node.textContent=next});
    };
    removeLegacyDeliverable(); new MutationObserver(removeLegacyDeliverable).observe(document.body,{childList:true,subtree:true});
  }
  window.addEventListener('load',unifyCreateArea);
})();

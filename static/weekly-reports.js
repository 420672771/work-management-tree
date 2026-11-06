function createWeeklyAutosave({save,onState=()=>{},delay=700,canEdit=value=>['pending','reviewing'].includes(value.status)}) {
  let current=null,dirty=false,sequence=0,epoch=0,timer=null,flight=null,error=null;
  const clone=value=>JSON.parse(JSON.stringify(value));
  function reset(value) {
    clearTimeout(timer);timer=null;epoch++;current=value;dirty=false;sequence=0;flight=null;error=null;
    onState('saved');
  }
  function edit() {
    if(!current || !canEdit(current))return;
    sequence++;dirty=true;clearTimeout(timer);
    if(error?.status===409){onState('error',error);return;}
    onState('saving');timer=setTimeout(flush,delay);
  }
  function flush() {
    clearTimeout(timer);timer=null;
    if(flight)return flight;
    if(!dirty || !current)return Promise.resolve(true);
    if(error?.status===409)return Promise.resolve(false);
    const capturedEpoch=epoch,target=current;
    let operation;
    operation=(async()=>{
      while(dirty && epoch===capturedEpoch){
        const capturedSequence=sequence;
        const payload={...clone(target.editorial),action:'save',edit_revision:target.edit_revision};
        onState('saving');
        try {
          const result=await save(target.id,payload);
          if(epoch!==capturedEpoch)return false;
          const latestEditorial=target.editorial;
          Object.assign(target,result);
          if(sequence!==capturedSequence)target.editorial=latestEditorial;
          else dirty=false;
          error=null;
        }catch(cause){
          if(epoch===capturedEpoch){error=cause;onState('error',cause);}
          return false;
        }
      }
      if(epoch!==capturedEpoch)return false;
      clearTimeout(timer);timer=null;
      onState('saved');return true;
    })().finally(()=>{if(flight===operation)flight=null;});
    flight=operation;return operation;
  }
  return {reset,edit,flush,get dirty(){return dirty;},get error(){return error;}};
}
if(typeof module!=='undefined' && module.exports)module.exports={createWeeklyAutosave};

(() => {
  if(typeof document==='undefined')return;
  'use strict';
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stateNames = {pending:'待发布', reviewing:'待发布', published:'已发布', superseded:'已替代草稿'};
  const workNames = {assigned:'待开始', in_progress:'进行中', awaiting_acceptance:'待验收', completed:'已完成'};
  let report=null,actorId=null,manager=false,active=false,requestVersion=0,noticeVersion=0,busy=false,availableChanges=null,actionConflict=null;
  const style = document.createElement('style');
  style.textContent = `#weeklyPage .weekly-toolbar,#weeklyPage .weekly-actions{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:16px 0}#weeklyPage button{width:auto;margin:0}#weeklyPage .weekly-toolbar label{margin:0}#weeklyPage .weekly-toolbar input{width:150px}#weeklyPage .weekly-toolbar select{min-width:150px}.weekly-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0}#weeklyPage .weekly-metric{background:#f7faff;color:#334155;border:1px solid #dce5f3;padding:14px;text-align:left;width:100%;border-radius:8px}#weeklyPage .weekly-metric b{display:block;color:#245dcc;font-size:22px;margin-bottom:5px}#weeklyPage .weekly-metric[aria-expanded=true]{border-color:#2563eb}.weekly-item{padding:16px;border:1px solid #e5eaf2;border-radius:8px;margin:12px 0;overflow-wrap:anywhere}.weekly-item h4{font-size:16px;margin:0 0 8px}.weekly-item p{margin:8px 0;white-space:pre-wrap}.weekly-item textarea{min-height:60px}#weeklyPage textarea{box-sizing:border-box;width:100%;font:inherit}#weeklyPage .weekly-section{margin:24px 0}#weeklyPage .weekly-section h3{font-size:20px}.weekly-history{overflow:auto}.weekly-history table{width:100%;border-collapse:collapse;white-space:nowrap}.weekly-history td,.weekly-history th{text-align:left;padding:10px;border-bottom:1px solid #e5eaf2}.weekly-notice{padding:14px 18px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;margin:16px 0}.weekly-notice button{width:auto;margin:0 0 0 12px}.weekly-meta{color:#667085;font-size:13px;line-height:1.8;white-space:pre-wrap}.weekly-summary{white-space:pre-wrap;line-height:1.8}.weekly-order{display:flex;gap:8px;justify-content:flex-end}.weekly-error{color:#b42318;white-space:pre-wrap}#weeklyPage .weekly-editor label{display:block;margin:14px 0 6px}@media(max-width:850px){.weekly-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:500px){.weekly-metrics{grid-template-columns:1fr}#weeklyPage .weekly-toolbar{align-items:stretch}#weeklyPage .weekly-toolbar input{width:100%}}`;
  style.textContent += `
    #weeklyPage .weekly-items{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start;margin-top:14px}
    #weeklyPage .weekly-items .weekly-item{min-width:0;margin:0;padding:14px 16px}
    #weeklyPage .weekly-item h4{line-height:1.5;margin-bottom:6px}
    #weeklyPage .weekly-item p{margin-top:6px;line-height:1.65}
    #weeklyPage .weekly-domain-group{min-width:0}
    #weeklyPage .weekly-domain-group>h4{margin:0 0 10px;font-size:14px;color:#315a92}
    #weeklyPage .weekly-domain-items{display:grid;gap:12px}
    #weeklyPage .weekly-report-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:12px 0 20px}
    #weeklyPage .weekly-report-meta .tag{margin:0}
    #weeklyPage .weekly-report-meta>.weekly-meta{white-space:normal}
    #weeklyPage .weekly-report-info{font-size:12px;color:#63738b}
    #weeklyPage .weekly-report-info>summary{cursor:pointer;color:#245dcc;width:max-content}
    #weeklyPage .weekly-report-info[open]{flex-basis:100%}
    #weeklyPage .weekly-report-explanation{padding:10px 14px;margin-top:8px;background:#f7f9fc;border-radius:6px;line-height:1.8}
    #weeklyPage .weekly-report-explanation p{margin:4px 0}
    #weeklyPage .weekly-more{position:relative;color:#63738b}#weeklyPage .weekly-more summary{cursor:pointer;padding:8px}#weeklyPage .weekly-more[open]{flex-basis:100%}#weeklyPage .weekly-actions [hidden]{display:none!important}
    #weeklyPage .weekly-order{flex-wrap:wrap;margin-top:10px}
    #weeklyPage .weekly-item label{display:block;margin-top:10px;font-size:13px;color:#63738b}
    @media(max-width:960px){#weeklyPage .weekly-items{grid-template-columns:minmax(0,1fr)}}
    @media print{#weeklyPage .weekly-items{grid-template-columns:minmax(0,1fr)}#weeklyPage .weekly-item{break-inside:avoid}}
  `;
  document.head.append(style);
  const nav = $('.nav-tabs');
  if (!nav) return;
  const tab = $('#weeklyTab') || document.createElement('button');
  if(!tab.id){tab.id='weeklyTab';tab.type='button';tab.textContent='部门周报';tab.className='hidden';nav.insertBefore(tab,$('#orgTab'));}
  const page = document.createElement('section'); page.id = 'weeklyPage'; page.className = 'hidden';
  page.innerHTML = `<main><section class="card"><h2>部门周报</h2><p class="muted">每周日 18:00 自动生成，打开时自动补齐本周报告。修改自动保存，负责人确认后即可发布。</p><nav class="section-subnav"><button type="button" id="weeklyCurrentTab" class="active">本周周报</button><button type="button" id="weeklyHistoryTab">历史周报</button></nav><div id="weeklyCurrent"><div id="weeklyActions" class="weekly-actions"></div><div id="weeklyDataNotice"></div><div id="weeklyContent"></div></div><div id="weeklyHistory" class="hidden"><div class="weekly-toolbar"><label>年份<input id="weeklyYear" type="number" min="1" max="9999" placeholder="全部"></label><label>月份<select id="weeklyMonth"><option value="">全部</option>${Array.from({length:12},(_,i)=>`<option value="${i+1}">${i+1} 月</option>`).join('')}</select></label><label>周次<input id="weeklyWeek" type="number" min="1" max="53" placeholder="ISO 周次"></label><button id="weeklyHistoryQuery" type="button" class="secondary">查询历史</button></div><p class="muted">月份按报告开始日期筛选；指定周次时，年份按 ISO 周所属年份筛选。</p><div id="weeklyHistoryContent" class="weekly-history"></div></div><p id="weeklyMessage" role="status" aria-live="polite"></p></section></main>`;
  document.body.append(page);
  const notice = document.createElement('div'); notice.className = 'weekly-notice hidden'; notice.id = 'weeklyNotice';
  $('#homePage')?.prepend(notice);
  const chinaDate = () => new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai'}).format(new Date());
  const monday = day => { const d = new Date(day+'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - (d.getUTCDay()+6)%7); return d.toISOString().slice(0,10); };
  const message = (text, error=false) => { $('#weeklyMessage').textContent = text; $('#weeklyMessage').className = error ? 'weekly-error' : 'muted'; };
  async function api(path='', body) {
    const capturedActor = actorId;
    const url = '/api/weekly-reports'+path+(body ? '' : (path.includes('?') ? '&' : '?')+'actor_id='+capturedActor);
    const res = await fetch(url, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,actor_id:capturedActor})} : {cache:'no-store'});
    const data = await res.json();
    if (capturedActor !== actorId) throw new Error('查看身份已变更，请重新加载周报');
    if(!res.ok){const error=new Error(data.error||'操作失败');error.status=res.status;throw error;}
    return data;
  }
  function displayDate(value) { return value ? String(value).replace('T',' ').replace(/\.\d+/, '') : '—'; }
  function reportMeta(value){
    const date=new Date(value.snapshot.data_cutoff);
    const cutoff=Number.isNaN(date.getTime())?'—':new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);
    return `<div class="weekly-report-meta"><span>${escape(value.period_start)}—${escape(value.period_end)}</span><span class="tag">${escape(stateNames[value.status])}</span><span class="tag">V${value.version}</span><span class="weekly-meta">数据截至 ${escape(cutoff)}</span><details class="weekly-report-info"><summary>报告说明</summary><div class="weekly-report-explanation"><p>生成时间：${escape(displayDate(value.generated_at))}</p>${value.status==='published'?`<p>发布人：${escape(value.publisher_name||'—')} · 发布时间：${escape(displayDate(value.published_at))}</p>`:''}${[value.snapshot.rules.cutoff,value.snapshot.rules.completion_time].filter(Boolean).map(rule=>`<p>${escape(rule)}</p>`).join('')}</div></details></div>`;
  }
  const editable = () => report && ['pending','reviewing'].includes(report.status);
  const taskMap = () => new Map(report.snapshot.tasks.map(t => [t.id,t]));
  const ordered = key => report.editorial.orders?.[key] || report.snapshot.sections[key];
  function sectionCards(key, tasks) {
    const ids=ordered(key);
    if(key!=='achievements')return ids.map((id,i)=>taskCard(tasks.get(id),key,i,ids.length)).join('');
    const groups=[];
    ids.forEach((id,i)=>{const task=tasks.get(id);let group=groups[groups.length-1];
      if(!group || group.domain!==task.domain_name){group={domain:task.domain_name,cards:[]};groups.push(group);}
      group.cards.push(taskCard(task,key,i,ids.length));
    });
    return groups.map(group=>`<section class="weekly-domain-group"><h4>${escape(group.domain)}</h4><div class="weekly-domain-items">${group.cards.join('')}</div></section>`).join('');
  }
  function taskCard(t, section, index, total) {
    return `<article class="weekly-item"><h4>#${t.id} ${escape(t.title)}</h4><p class="weekly-meta">${escape(t.owner_name || '未知责任人')} · ${escape(t.domain_name)} · ${escape(t.priority)} · ${escape(workNames[t.work_status] || t.work_status)} · 截止 ${escape(t.due_at?.slice(0,10))}</p><p>交付物/验收标准：${escape(t.deliverable || '未填写')}</p>${section==='risks' ? `<p>关注原因：${escape(t.risk_reasons.join('；'))}</p><p>风险/最后进展：${escape(t.block_reason || t.latest_progress || '暂无进展记录')}</p><p>建议动作：${escape(t.suggested_action)}</p>` : ''}${section==='achievements' ? (t.completed_at ? `<p class="weekly-meta">完成时间：${escape(displayDate(t.completed_at))}（${escape(t.completion_source)}）</p>` : `<p>最后进展：${escape(t.latest_progress || '暂无进展记录')}</p>`) : ''}${editable() && section ? `<label>补充说明<textarea data-note="${t.id}" maxlength="4000">${escape(report.editorial.notes?.[t.id] || '')}</textarea></label><div class="weekly-order"><button type="button" class="secondary" data-section="${section}" data-index="${index}" data-move="-1" ${index===0?'disabled':''} aria-label="上移 ${escape(t.title)}">上移</button><button type="button" class="secondary" data-section="${section}" data-index="${index}" data-move="1" ${index===total-1?'disabled':''} aria-label="下移 ${escape(t.title)}">下移</button></div>` : report.editorial.notes?.[t.id] ? `<p>补充说明：${escape(report.editorial.notes[t.id])}</p>` : ''}</article>`;
  }
  function renderReport() {
    renderActions();renderChanges();
    const box = $('#weeklyContent');
    if (!report) { box.innerHTML = '<p class="muted">正在打开本周周报…</p>'; return; }
    const s=report.snapshot, e=report.editorial, m=s.metrics, tasks=taskMap();
    const numbers=[['new','本周新增任务',m.new],['completed','本周完成任务',m.completed],['in_progress','进行中任务',m.in_progress],['overdue','已逾期任务',m.overdue],['awaiting_acceptance','待验收任务',m.awaiting_acceptance],['hours','完成标准 / 实际工时',`${m.completed_hours.standard}h / ${m.completed_hours.actual}h`],['domains','责任域覆盖',m.domains]];
    box.innerHTML = `<h2>${escape(s.title)}</h2>${reportMeta(report)}<div class="weekly-editor"><label>本周一句话摘要${editable()?`<textarea data-editorial="summary" maxlength="10000">${escape(e.summary ?? s.summary)}</textarea>`:`<p class="weekly-summary">${escape(e.summary ?? s.summary)}</p>`}</label></div><div class="weekly-metrics">${numbers.map(([key,label,value])=>`<button type="button" class="weekly-metric" data-metric="${key}" aria-expanded="false"><b>${escape(value)}</b>${label}</button>`).join('')}</div><div id="weeklyMetricDetails"></div>${[['achievements','本周重点工作与成果'],['risks','任务进展与风险'],['next_week','下周重点计划']].map(([key,title])=>{const ids=ordered(key);return `<section class="weekly-section"><h3>${title}</h3>${key==='achievements' && s.rules.achievements ? `<p class="muted">${escape(s.rules.achievements)}</p>` : ''}${ids.length ? `<div class="weekly-items">${sectionCards(key,tasks)}</div>` : '<p class="muted">暂无符合条件的事项。</p>'}</section>`;}).join('')}<section class="weekly-section weekly-editor"><h3>负责人总结</h3>${[['conclusion','本周管理结论'],['requirements','下周工作要求'],['support','需上级或其他部门支持']].map(([key,label])=>`<label>${label}${editable()?`<textarea data-editorial="${key}" maxlength="10000">${escape(e[key] || '')}</textarea>`:`<p class="weekly-summary">${escape(e[key] || '未补充')}</p>`}</label>`).join('')}</section><p class="muted">重点工作与成果按责任域归类。统计数字及展开任务均来自此版本快照，后续任务修改或删除不会改写本报告。</p>`;
  }
  function saveState(state,error){
    const status=$('#weeklySaveStatus');
    if(status){status.textContent=state==='error'?(error?.status===409?'编辑冲突，当前输入已保留':'保存失败，当前输入已保留'):state==='saving'?'保存中…':'已自动保存';status.className=state==='error'?'weekly-error':'muted';}
    const retry=$('#weeklyRetry');if(retry)retry.hidden=state!=='error'||error?.status===409;
    const reload=$('#weeklyConflictReload');if(reload)reload.hidden=state!=='error'||error?.status!==409;
    const publish=$('#weeklyPublish');if(publish)publish.disabled=busy||state==='error';
  }
  const autosave=createWeeklyAutosave({save:(id,payload)=>api('/'+id,payload),onState:saveState});
  function renderActions(){
    const box=$('#weeklyActions');
    if(!report){box.innerHTML='';return;}
    const disabled=busy?'disabled':'',isCurrent=report.period_start===monday(chinaDate());
    box.innerHTML=`${editable()&&manager?`<button type="button" id="weeklyPublish" data-action="publish" ${disabled}>发布周报</button>`:''}${report.status==='published'&&manager?`<button type="button" class="secondary" data-action="revise" ${disabled}>修订</button>`:''}<button type="button" class="secondary" data-action="copy" ${disabled}>复制会议材料</button>${!isCurrent?`<button type="button" class="secondary" data-action="current" ${disabled}>返回本周</button>`:''}<details class="weekly-more"><summary>更多操作</summary><div class="weekly-toolbar">${editable()?`<button type="button" class="secondary" data-action="refresh" ${disabled}>更新任务数据</button>`:''}<button type="button" class="secondary" data-action="reload" ${disabled}>重新加载</button><button type="button" class="secondary" data-action="demo" ${disabled}>查看演示周报</button></div></details>${editable()?`<span id="weeklySaveStatus" class="muted" role="status" aria-live="polite">已自动保存</span><button id="weeklyRetry" type="button" class="secondary" data-action="retry" hidden>重试保存</button><button id="weeklyConflictReload" type="button" class="secondary" data-action="reload" hidden>重新加载</button>${!manager?'<span class="muted">由部门负责人最终发布</span>':''}`:''}`;
    if(autosave.error||actionConflict)saveState('error',autosave.error||actionConflict);else if(autosave.dirty)saveState('saving');
  }
  function renderChanges(){
    const box=$('#weeklyDataNotice');
    if(!report||!editable()){box.innerHTML='';return;}
    const fresh=availableChanges?.has_changes,changes=fresh?availableChanges:report.snapshot.change_summary;
    if(!changes?.has_changes){box.innerHTML='';return;}
    const counts=`新增 ${changes.added.length} 项、变化 ${changes.changed.length} 项、移除 ${changes.removed.length} 项`;
    const groups=[['added','新增'],['changed','变化'],['removed','移除']];
    box.innerHTML=`<div class="weekly-notice"><span>${fresh?'任务数据有更新':'最近一次任务数据更新'}：${counts}。${changes.rules_changed?'取数口径有更新。':''}</span>${fresh?`<button type="button" class="secondary" data-action="refresh" ${busy?'disabled':''}>更新任务数据</button>`:''}<details><summary>查看变化事项</summary>${groups.map(([key,label])=>changes[key].length?`<p>${label}</p><ul>${changes[key].map(t=>`<li>#${t.id} ${escape(t.title)} · ${escape(t.owner_name||'未知责任人')}</li>`).join('')}</ul>`:'').join('')}${changes.metrics_changed?'<p class="muted">统计数字发生变化。</p>':''}${changes.sections_changed?'<p class="muted">重点工作、风险或下周计划的入选任务发生变化。</p>':''}</details></div>`;
  }
  function setReport(value){report=value;availableChanges=null;actionConflict=null;autosave.reset(value);renderReport();}
  async function readyToLeave(){
    if(busy){message('正在处理周报，请稍候。');return false;}
    const saved=await autosave.flush();
    if(!saved)message('修改尚未保存成功，请先重试保存或处理编辑冲突。',true);
    return saved;
  }
  async function checkChanges(){
    if(!report||!editable())return;
    const id=report.id,identity=actorId,revision=report.edit_revision;
    try{const changes=await api('/'+id+'/changes');if(report?.id===id&&actorId===identity&&report.edit_revision===revision){availableChanges=changes;renderChanges();}}catch(_){/* A failed advisory check never replaces the report or editing state. */}
  }
  async function refreshNotice(){
    const version=++noticeVersion;
    if(!actorId||!manager){notice.classList.add('hidden');return;}
    try{
      const data=await api();if(!manager||version!==noticeVersion)return;
      const pending=data.pending.find(p=>p.period_start===data.current_period_start)||data.pending[0];
      notice.classList.toggle('hidden',!pending);
      if(pending){notice.textContent=pending.period_start===data.current_period_start?'本周部门周报待发布，请查看后发布。':`${pending.period_start} 起的部门周报待发布。`;const button=document.createElement('button');button.type='button';button.textContent='查看周报';button.onclick=async()=>{await openPage('weekly');if(report?.id!==pending.id)await loadReport(pending.id);};notice.append(button);}
    }catch(_){notice.classList.add('hidden');}
  }
  async function loadCurrent(){
    if(!await readyToLeave())return;
    const version=++requestVersion,identity=actorId;message('');busy=true;renderActions();$('#weeklyContent').inert=true;
    try{const data=await api('/current',{});if(version!==requestVersion)return;setReport(data);checkChanges();refreshNotice();}
    catch(error){message(error.message,true);}
    finally{if(version===requestVersion&&identity===actorId){busy=false;$('#weeklyContent').inert=false;renderActions();renderChanges();}}
  }
  async function loadReport(id,discard=false){
    if(!discard&&!await readyToLeave())return;
    const version=++requestVersion,identity=actorId;busy=true;renderActions();$('#weeklyContent').inert=true;
    try{const data=await api('/'+id);if(version!==requestVersion)return;setReport(data);checkChanges();}
    catch(error){message(error.message,true);}
    finally{if(version===requestVersion&&identity===actorId){busy=false;$('#weeklyContent').inert=false;renderActions();renderChanges();}}
  }
  async function reloadReport(){
    if(busy)return;
    if(autosave.dirty&&!window.confirm('当前修改尚未保存，重新加载会放弃这些输入。是否重新加载？'))return;
    if(report)await loadReport(report.id,true);else await loadCurrent();
  }
  async function mutate(action){
    if(busy||!report)return;
    busy=true;const identity=actorId,version=requestVersion;
    renderActions();$('#weeklyContent').inert=true;
    try{
      if(!await autosave.flush()){message('修改尚未保存成功，本次操作未执行。',true);return;}
      if(identity!==actorId||version!==requestVersion)return;
      let data;
      if(action==='publish')data=await api('/'+report.id,{action:'publish',edit_revision:report.edit_revision});
      else if(action==='refresh')data=await api('/'+report.id+'/refresh',{edit_revision:report.edit_revision});
      else data=await api('/'+report.id+'/revise',{});
      if(identity!==actorId||version!==requestVersion)return;
      setReport(data);message(action==='publish'?'周报已发布。':action==='refresh'?'任务数据已更新，人工补充内容已保留。':'修订稿已打开，修改将自动保存。');refreshNotice();
    }catch(error){message(error.message,true);if(error.status===409){actionConflict=error;saveState('error',error);}}
    finally{if(actorId===identity&&version===requestVersion){busy=false;$('#weeklyContent').inert=false;renderActions();renderChanges();}}
  }
  async function copyMaterial(){
    if(!report||!await readyToLeave())return;
    try{const data=await api('/'+report.id+'/text');
      if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(data.text);message('会议材料已复制。');return;}catch(_){}}
      const area=document.createElement('textarea');area.value=data.text;area.readOnly=true;area.setAttribute('aria-label','会议材料文本');$('#weeklyContent').prepend(area);area.focus();area.select();message('请复制已选中的会议材料文本。');
    }catch(error){message(error.message,true);}
  }
  async function actionClick(event){
    const button=event.target.closest('[data-action]');if(!button||busy)return;
    const action=button.dataset.action;
    if(['publish','refresh','revise'].includes(action))await mutate(action);
    else if(action==='copy')await copyMaterial();
    else if(action==='retry')await autosave.flush();
    else if(action==='reload')await reloadReport();
    else if(action==='current')await loadCurrent();
    else if(action==='demo')window.open('/weekly-report-demo?actor_id='+encodeURIComponent(actorId),'_blank','noopener');
  }
  $('#weeklyActions').addEventListener('click',actionClick);$('#weeklyDataNotice').addEventListener('click',actionClick);
  $('#weeklyContent').addEventListener('input',event=>{
    if(!editable()||busy)return;
    if(event.target.dataset.editorial)report.editorial[event.target.dataset.editorial]=event.target.value;
    else if(event.target.dataset.note){report.editorial.notes||={};report.editorial.notes[event.target.dataset.note]=event.target.value;document.querySelectorAll(`[data-note="${event.target.dataset.note}"]`).forEach(el=>{if(el!==event.target)el.value=event.target.value;});}
    else return;
    autosave.edit();
  });
  $('#weeklyContent').addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button||!report||busy)return;
    if(button.dataset.metric){const key=button.dataset.metric,expanded=button.getAttribute('aria-expanded')==='true';document.querySelectorAll('[data-metric]').forEach(b=>b.setAttribute('aria-expanded','false'));$('#weeklyMetricDetails').innerHTML='';if(expanded)return;button.setAttribute('aria-expanded','true');const ids=report.snapshot.metric_tasks[key==='hours'?'completed':key]||[],tasks=taskMap();$('#weeklyMetricDetails').innerHTML=`<h3>${escape(button.textContent)}</h3>${ids.length?`<div class="weekly-items">${ids.map(id=>taskCard(tasks.get(id),'',0,0)).join('')}</div>`:'<p class="muted">暂无对应任务。</p>'}`;}
    if(button.dataset.move&&editable()){const key=button.dataset.section,ids=[...ordered(key)],i=Number(button.dataset.index),to=i+Number(button.dataset.move);if(to<0||to>=ids.length)return;[ids[i],ids[to]]=[ids[to],ids[i]];report.editorial.orders||={};report.editorial.orders[key]=ids;autosave.edit();renderReport();}
  });
  async function history(){
    try{const q=new URLSearchParams();for(const [key,id] of [['year','weeklyYear'],['month','weeklyMonth'],['week','weeklyWeek']])if($('#'+id).value)q.set(key,$('#'+id).value);const data=await api('?'+q.toString()),items=data.items.filter(r=>r.status!=='superseded');
      $('#weeklyHistoryContent').innerHTML=items.length?`<table><thead><tr><th>报告周期</th><th>版本 / 状态</th><th>生成时间</th><th>发布人 / 时间</th><th>操作</th></tr></thead><tbody>${items.map(r=>`<tr><td>${escape(r.period_start)}—${escape(r.period_end)}</td><td>V${r.version} · ${escape(stateNames[r.status])}</td><td>${escape(displayDate(r.generated_at))}</td><td>${escape(r.publisher_name||'—')}<br>${escape(displayDate(r.published_at))}</td><td><button type="button" class="secondary" data-report="${r.id}">查看</button></td></tr>`).join('')}</tbody></table>`:'<p class="muted">暂无符合条件的历史周报。</p>';
    }catch(error){message(error.message,true);}
  }
  async function showTab(historyView){
    if(!await readyToLeave())return;
    $('#weeklyCurrent').classList.toggle('hidden',historyView);$('#weeklyHistory').classList.toggle('hidden',!historyView);$('#weeklyCurrentTab').classList.toggle('active',!historyView);$('#weeklyHistoryTab').classList.toggle('active',historyView);
    if(historyView)await history();else await loadCurrent();
  }
  $('#weeklyCurrentTab').onclick=()=>showTab(false);$('#weeklyHistoryTab').onclick=()=>showTab(true);$('#weeklyHistoryQuery').onclick=history;
  $('#weeklyHistoryContent').onclick=async event=>{const b=event.target.closest('[data-report]');if(!b||!await readyToLeave())return;$('#weeklyCurrent').classList.remove('hidden');$('#weeklyHistory').classList.add('hidden');$('#weeklyCurrentTab').classList.add('active');$('#weeklyHistoryTab').classList.remove('active');await loadReport(Number(b.dataset.report));};
  const baseOpen=window.openPage;
  window.openPage=async function(name){
    if(active&&name!=='weekly'&&!await readyToLeave())return;
    if(name==='weekly'){
      if(!actorId)return;
      for(const id of ['homePage','taskPage','approvalPage','orgPage','performancePage'])$('#'+id)?.classList.add('hidden');
      page.classList.remove('hidden');document.querySelectorAll('.nav-tabs button').forEach(b=>b.classList.toggle('active',b===tab));
      if(!active){active=true;await showTab(false);}return;
    }
    active=false;page.classList.add('hidden');baseOpen(name);tab.classList.remove('active');if(name==='home')refreshNotice();
  };
  window.openDepartmentWeeklyReport=()=>openPage('weekly');tab.onclick=window.openDepartmentWeeklyReport;
  function refreshAccess(){
    const id=typeof currentActorId==='function'?currentActorId():0,user=typeof currentUsers!=='undefined'?currentUsers.find(u=>Number(u.id)===Number(id)):null,allowed=user&&['manager','pm'].includes(user.role);
    if(actorId===(allowed?id:null)&&manager===(user?.role==='manager'))return;
    actorId=allowed?id:null;manager=user?.role==='manager';++requestVersion;busy=false;setReport(null);$('#weeklyContent').inert=false;$('#weeklyHistoryContent').textContent='';
    tab.hidden=!allowed;tab.classList.toggle('hidden',!allowed);tab.style.setProperty('display',allowed?'inline-flex':'none','important');
    refreshNotice();if(!allowed&&active){active=false;page.classList.add('hidden');baseOpen('home');}else if(active)loadCurrent();
  }
  const baseActor=window.renderDemoActor;if(baseActor)window.renderDemoActor=function(...args){const out=baseActor(...args);refreshAccess();return out;};
  document.addEventListener('change',event=>{if(event.target.id==='demoActor')setTimeout(refreshAccess,0);});
  window.addEventListener('wmt:identity-changed',refreshAccess);window.addEventListener('load',refreshAccess);
  window.addEventListener('beforeunload',event=>{if(autosave.dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',()=>{if(autosave.dirty)autosave.flush();});
  setInterval(()=>{if(!document.hidden&&manager&&!busy)refreshNotice();if(!document.hidden&&active&&!busy)checkChanges();},60000);
  refreshAccess();
})();

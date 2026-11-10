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
    #weeklyPage .weekly-items{display:grid;grid-template-columns:minmax(0,1fr);gap:18px;align-items:start;margin-top:16px}
    #weeklyPage .weekly-items .weekly-item{min-width:0;margin:0;padding:22px 24px;border-radius:14px;background:#fff;border-color:#e0e7f0;box-shadow:0 2px 7px #18335404}
    #weeklyPage .weekly-item h4{line-height:1.5;margin-bottom:6px}
    #weeklyPage .weekly-item p{margin-top:6px;line-height:1.65}
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
    #weeklyPage .weekly-task-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
    #weeklyPage .weekly-task-heading h4{margin:0;min-width:0;font-size:19px;line-height:1.5;color:#183354;letter-spacing:.1px}
    #weeklyPage .weekly-task-badges{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px;flex-shrink:0;max-width:45%}
    #weeklyPage .weekly-chip{display:inline-flex;align-items:center;padding:4px 9px;border-radius:6px;background:#f2f5f9;color:#56677d;font-size:12px;font-weight:600;line-height:1.5;white-space:nowrap}
    #weeklyPage .weekly-chip.priority-p0{background:#fff1ec;color:#a63f27}#weeklyPage .weekly-chip.status-in_progress{background:#edf4ff;color:#285fc0}
    #weeklyPage .weekly-chip.status-completed{background:#eaf7f0;color:#168052}#weeklyPage .weekly-chip.status-awaiting_acceptance{background:#f2efff;color:#6a4cb0}
    #weeklyPage .weekly-chip.attention{background:#fff0ee;color:#b34135}#weeklyPage .weekly-chip.watch{background:#fff7e8;color:#95651c}
    #weeklyPage .weekly-task-update{margin:16px 0 18px}#weeklyPage .weekly-task-update p{margin:5px 0 0;font-size:14px;color:#40526a;line-height:1.85}
    #weeklyPage .weekly-task-caption{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:#7b899c;font-size:12px;font-weight:500}
    #weeklyPage .weekly-task-caption time{font-size:11px;color:#8a96a7}
    #weeklyPage .weekly-task-progress{display:flex;align-items:center;gap:13px;margin:16px 0 14px}
    #weeklyPage .weekly-progress-label{color:#718096;font-size:12px;white-space:nowrap}
    #weeklyPage .weekly-progress-track{flex:1;height:7px;border-radius:999px;background:#edf1f6;overflow:hidden;min-width:36px}
    #weeklyPage .weekly-progress-track>span{display:block;height:100%;border-radius:inherit;background:#4c7bda}
    #weeklyPage .weekly-item.is-completed .weekly-progress-track>span{background:#20a778}
    #weeklyPage .weekly-progress-value{color:#365575;min-width:34px;text-align:right;font-size:14px;font-weight:650;font-variant-numeric:tabular-nums}
    #weeklyPage .weekly-progress-unknown{font-size:12px;color:#8a96a7;white-space:nowrap}
    #weeklyPage .weekly-task-facts{display:flex;flex-wrap:wrap;gap:8px 22px;margin:12px 0 0;padding-top:14px;border-top:1px solid #edf0f5;font-size:12px;color:#758298}
    #weeklyPage .weekly-task-facts b{font-weight:500;color:#52647c;margin-left:6px}
    #weeklyPage .weekly-task-delivery{display:grid;grid-template-columns:120px minmax(0,1fr);gap:8px;margin-top:14px;font-size:13px;line-height:1.75;color:#617087}
    #weeklyPage .weekly-task-delivery>span{color:#7b899c;font-size:12px}#weeklyPage .weekly-task-delivery p{margin:0;font-size:13px;white-space:pre-wrap}
    #weeklyPage .weekly-task-next{display:flex;align-items:flex-start;gap:14px;padding:12px 15px;margin-top:17px;background:#f2f6fd;border-left:3px solid #6c8fd5;border-radius:0 8px 8px 0;font-size:13px;line-height:1.8}
    #weeklyPage .weekly-task-next>b{flex-shrink:0;color:#3e65aa;font-size:12px;padding-top:1px}#weeklyPage .weekly-task-next p{margin:0;color:#4a607e;white-space:pre-wrap}
    #weeklyPage .weekly-task-risk{margin-top:17px;padding:16px 0 0;border-top:1px dashed #e4e9f1}
    #weeklyPage .weekly-task-risk h5{margin:0 0 9px;color:#a64b33;font-size:13px;font-weight:650}
    #weeklyPage .weekly-risk-tags{display:flex;flex-wrap:wrap;gap:7px}#weeklyPage .weekly-risk-tags>span{padding:3px 8px;border-radius:5px;background:#fff7ed;color:#976236;font-size:12px;line-height:1.6}
    #weeklyPage .weekly-task-block{display:flex;gap:12px;padding:11px 13px;border-radius:7px;background:#fff5f3;margin-top:10px;font-size:13px;line-height:1.8}
    #weeklyPage .weekly-task-block b{color:#a64939;flex-shrink:0}#weeklyPage .weekly-task-block p{margin:0;color:#77564f}
    #weeklyPage .weekly-task-footer{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-top:18px;padding-top:12px;border-top:1px solid #edf0f5}
    #weeklyPage .weekly-note-editor{flex:1;min-width:0;color:#63738b;font-size:12px}#weeklyPage .weekly-note-editor>summary{cursor:pointer;line-height:30px;width:fit-content}
    #weeklyPage .weekly-note-editor[open]>summary{color:#315f9e}#weeklyPage .weekly-note-editor label{margin-top:6px}#weeklyPage .weekly-note-editor textarea{min-height:76px;margin-top:5px}
    #weeklyPage .weekly-task-footer .weekly-order{margin:0;flex-shrink:0}#weeklyPage .weekly-task-footer button{padding:6px 10px;font-size:12px}
    #weeklyPage .weekly-task-note{margin-top:16px;padding:12px 14px;border-radius:8px;background:#f7f9fc;font-size:13px;line-height:1.8;color:#53677f;white-space:pre-wrap}
    #weeklyPage .weekly-task-note b{display:block;margin-bottom:3px;font-size:12px;color:#728197}
    #weeklyPage .weekly-section-heading{display:flex;align-items:center;gap:10px;margin:26px 0 12px}#weeklyPage .weekly-section-heading h3{margin:0}
    #weeklyPage .weekly-section-count{font-size:12px;font-weight:500;color:#8190a4;padding:2px 8px;background:#f1f5fa;border-radius:999px}
    @media(max-width:640px){#weeklyPage .weekly-items .weekly-item{padding:17px 16px;border-radius:11px}#weeklyPage .weekly-task-heading{flex-direction:column;gap:9px}#weeklyPage .weekly-task-heading h4{font-size:17px}#weeklyPage .weekly-task-badges{max-width:100%;justify-content:flex-start}#weeklyPage .weekly-task-facts{gap:8px 15px}#weeklyPage .weekly-task-delivery{grid-template-columns:1fr;gap:2px}#weeklyPage .weekly-task-footer{flex-direction:column;gap:10px}#weeklyPage .weekly-note-editor{width:100%}#weeklyPage .weekly-task-footer .weekly-order{align-self:flex-end}#weeklyPage .weekly-task-next{gap:10px}#weeklyPage .weekly-task-block{flex-direction:column;gap:2px}}
    @media(max-width:960px){#weeklyPage .weekly-items{grid-template-columns:minmax(0,1fr)}}
    @media print{#weeklyPage .weekly-items{grid-template-columns:minmax(0,1fr)}#weeklyPage .weekly-item{break-inside:avoid;box-shadow:none}#weeklyPage .weekly-order{display:none}}
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
  const ordered = key => report.editorial.orders?.[key] || [...report.snapshot.sections[key]].sort((a,b)=>a-b);
  function sectionCards(key, tasks) {
    const ids=ordered(key);
    return ids.map((id,i)=>taskCard(tasks.get(id),key,i,ids.length)).join('');
  }
  function taskCard(t, section, index, total) {
    const done=t.work_status==='completed',reasons=Array.isArray(t.risk_reasons)?t.risk_reasons:[],blocked=Boolean(t.block_reason)||t.risk_status==='blocked';
    const value=t.effective_percent,number=value===null||value===undefined||value===''?NaN:Number(value);
    const percent=done?100:Number.isFinite(number)&&number>=0&&number<=100?number:null;
    const note=report.editorial.notes?.[t.id]||'';
    const attention=!done&&(blocked?'有阻塞':t.overdue?'已逾期':t.risk_status==='at_risk'?'有风险':reasons.length?'需关注':'');
    const time=value=>{if(!value)return '';const date=new Date(value);return Number.isNaN(date.getTime())?String(value):new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);};
    const progress=percent===null?'<div class="weekly-progress-track" aria-hidden="true"></div><span class="weekly-progress-unknown">暂无已确认进度</span>':`<div class="weekly-progress-track" role="progressbar" aria-label="任务#${t.id}已确认进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="width:${percent}%"></span></div><strong class="weekly-progress-value">${percent}%</strong>`;
    const risk=!done&&(reasons.length||blocked)?`<div class="weekly-task-risk"><h5>风险与关注${reasons.length?` · ${reasons.length} 项关注条件`:''}</h5>${reasons.length?`<div class="weekly-risk-tags">${reasons.map(reason=>`<span>${escape(reason)}</span>`).join('')}</div>`:''}${t.block_reason?`<div class="weekly-task-block"><b>当前卡点</b><p>${escape(t.block_reason)}</p></div>`:''}</div>`:'';
    const editorial=editable()&&section?`<div class="weekly-task-footer"><details class="weekly-note-editor" ${note?'open':''}><summary>周报补充${note?' · 已填写':' · 添加说明'}</summary><label>补充说明<textarea data-note="${t.id}" maxlength="4000" placeholder="补充周会结论、协调事项或下一步安排">${escape(note)}</textarea></label></details><div class="weekly-order"><button type="button" class="secondary" data-section="${section}" data-index="${index}" data-move="-1" ${index===0?'disabled':''} aria-label="上移 ${escape(t.title)}">上移</button><button type="button" class="secondary" data-section="${section}" data-index="${index}" data-move="1" ${index===total-1?'disabled':''} aria-label="下移 ${escape(t.title)}">下移</button></div></div>`:note?`<div class="weekly-task-note"><b>周报补充</b>${escape(note)}</div>`:'';
    return `<article class="weekly-item weekly-task-card${done?' is-completed':''}" data-task-id="${t.id}">
      <div class="weekly-task-heading"><h4>#${t.id} ${escape(t.title)}</h4><div class="weekly-task-badges"><span class="weekly-chip ${t.priority==='P0'?'priority-p0':''}">${escape(t.priority||'未设优先级')}</span><span class="weekly-chip status-${escape(t.work_status)}">${escape(workNames[t.work_status]||t.work_status)}</span>${attention?`<span class="weekly-chip ${blocked||t.overdue?'attention':'watch'}">${attention}</span>`:''}</div></div>
      <div class="weekly-task-update"><div class="weekly-task-caption">最新进展${t.latest_progress_at?`<time>${escape(time(t.latest_progress_at))}</time>`:''}</div><p>${escape(t.latest_progress||'暂无进展记录')}</p></div>
      <div class="weekly-task-progress"><span class="weekly-progress-label">已确认进度</span>${progress}</div>
      <div class="weekly-task-facts"><span>责任人<b>${escape(t.owner_name||'未知责任人')}</b></span><span>责任域<b>${escape(t.domain_name||'未归类')}</b></span><span>计划完成<b>${escape(t.due_at?.slice(0,10)||'未设置')}</b></span>${t.completed_at?`<span title="${escape(t.completion_source)}">完成时间<b>${escape(time(t.completed_at))}</b></span>`:''}</div>
      <div class="weekly-task-delivery"><span>交付物 / 验收标准</span><p>${escape(t.deliverable||'未填写')}</p></div>
      ${!done&&t.suggested_action?`<div class="weekly-task-next"><b>建议下一步</b><p>${escape(t.suggested_action)}</p></div>`:''}${risk}${editorial}
    </article>`;
  }
  function renderReport() {
    renderActions();renderChanges();
    const box = $('#weeklyContent');
    if (!report) { box.innerHTML = '<p class="muted">正在打开本周周报…</p>'; return; }
    const s=report.snapshot, e=report.editorial, m=s.metrics, tasks=taskMap();
    const numbers=[['new','本周新增任务',m.new],['completed','本周完成任务',m.completed],['in_progress','进行中任务',m.in_progress],['overdue','已逾期任务',m.overdue],['awaiting_acceptance','待验收任务',m.awaiting_acceptance],['hours','完成标准 / 实际工时',`${m.completed_hours.standard}h / ${m.completed_hours.actual}h`],['domains','责任域覆盖',m.domains]];
    box.innerHTML = `<h2>${escape(s.title)}</h2>${reportMeta(report)}<div class="weekly-editor"><label>本周一句话摘要${editable()?`<textarea data-editorial="summary" maxlength="10000">${escape(e.summary ?? s.summary)}</textarea>`:`<p class="weekly-summary">${escape(e.summary ?? s.summary)}</p>`}</label></div><div class="weekly-metrics">${numbers.map(([key,label,value])=>`<button type="button" class="weekly-metric" data-metric="${key}" aria-expanded="false"><b>${escape(value)}</b>${label}</button>`).join('')}</div><div id="weeklyMetricDetails"></div>${[['achievements','本周重点工作与成果'],['risks','任务进展与风险'],['next_week','下周重点计划']].map(([key,title])=>{const ids=ordered(key);return `<section class="weekly-section"><h3>${title}</h3>${key==='achievements' && s.rules.achievements ? `<p class="muted">${escape(s.rules.achievements)}</p>` : ''}${ids.length ? `<div class="weekly-items">${sectionCards(key,tasks)}</div>` : '<p class="muted">暂无符合条件的事项。</p>'}</section>`;}).join('')}<section class="weekly-section weekly-editor"><h3>负责人总结</h3>${[['conclusion','本周管理结论'],['requirements','下周工作要求'],['support','需上级或其他部门支持']].map(([key,label])=>`<label>${label}${editable()?`<textarea data-editorial="${key}" maxlength="10000">${escape(e[key] || '')}</textarea>`:`<p class="weekly-summary">${escape(e[key] || '未补充')}</p>`}</label>`).join('')}</section><p class="muted">任务默认按编号从小到大展示，不再按责任域分组；可用上移、下移调整展示顺序。统计数字及展开任务均来自此版本快照，后续任务修改或删除不会改写本报告。</p>`;
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
    if(button.dataset.metric){const key=button.dataset.metric,expanded=button.getAttribute('aria-expanded')==='true';document.querySelectorAll('[data-metric]').forEach(b=>b.setAttribute('aria-expanded','false'));$('#weeklyMetricDetails').innerHTML='';if(expanded)return;button.setAttribute('aria-expanded','true');const ids=[...(report.snapshot.metric_tasks[key==='hours'?'completed':key]||[])].sort((a,b)=>a-b),tasks=taskMap();$('#weeklyMetricDetails').innerHTML=`<h3>${escape(button.textContent)}</h3>${ids.length?`<div class="weekly-items">${ids.map(id=>taskCard(tasks.get(id),'',0,0)).join('')}</div>`:'<p class="muted">暂无对应任务。</p>'}`;}
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

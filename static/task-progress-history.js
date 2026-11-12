// Reports are loaded only when a task is opened, using the task's existing scope.
(()=>{
  const escape=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const time=value=>{
    if(!value)return '未记录';
    const date=new Date(value);
    return Number.isNaN(date.getTime())?String(value):new Intl.DateTimeFormat('zh-CN',{
      timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false
    }).format(date);
  };
  const statuses={pending:'待确认',approved:'已确认',auto_approved:'自动生效',rejected:'已驳回'};
  function reportMarkup(row){
    const status=row.confirmation_status||row.status,label=statuses[status]||'状态未记录';
    const percent=Number(row.reported_percent),known=row.reported_percent!=null&&Number.isFinite(percent)&&percent>=0&&percent<=100;
    const source=({web:'网页',feishu:'飞书',feishu_ws:'飞书',wecom:'企业微信'})[row.source]||'其他来源';
    const decided=['approved','auto_approved','rejected'].includes(status);
    return `<li class="progress-history-item" data-progress-id="${escape(row.id)}">
      <div class="progress-history-row"><strong>填报比例 ${known?escape(percent)+'%':'未记录'}</strong><span class="progress-history-status ${Object.hasOwn(statuses,status)?status:''}">${label}</span></div>
      ${known?`<div class="progress-history-track" aria-label="本次填报 ${escape(percent)}%"><span style="width:${percent}%"></span></div>`:''}
      <p class="progress-history-meta">${escape(time(row.reported_at))} · ${escape(row.reporter_name||'提交人未记录')} · ${source}</p>
      <p class="progress-history-text">${escape(row.report_text||'未填写进度说明')}</p>
      ${row.is_blocked?`<p class="progress-history-blocked"><b>阻塞：</b>${escape(row.block_reason||'未填写原因')}</p>`:''}
      ${row.expected_finish?`<p class="progress-history-meta">预计完成：${escape(String(row.expected_finish).slice(0,10))}</p>`:''}
      ${decided?`<p class="progress-history-meta">${status==='auto_approved'?'系统超时自动生效':`${status==='rejected'?'驳回':'确认'}人：${escape(row.decision_name||'未记录')}`} · ${escape(time(row.decision_at))}</p>`:''}
      ${row.result_reason?`<p class="${status==='rejected'?'progress-history-blocked':'progress-history-meta'}">${status==='rejected'?'驳回原因':'处理说明'}：${escape(row.result_reason)}</p>`:''}
    </li>`;
  }
  function mount(id){
    const panel=document.querySelector('#taskDetailContent');
    if(!panel||panel.querySelector('#taskEditForm')||panel.querySelector('[data-history-task="'+Number(id)+'"]'))return;
    const section=document.createElement('section');section.className='task-progress-history';section.dataset.historyTask=String(id);
    section.innerHTML='<div class="progress-history-row"><h4>历史反馈 <span class="progress-history-count"></span></h4><button type="button" class="secondary small progress-history-refresh">刷新记录</button></div><p class="progress-history-meta">按提交时间倒序，填报比例经确认后生效。</p><div class="progress-history-content" aria-live="polite"></div>';
    panel.append(section);
    const jump=document.createElement('button');jump.type='button';jump.className='secondary small progress-history-jump';jump.textContent='查看历史反馈';
    jump.addEventListener('click',()=>section.scrollIntoView({block:'start'}));panel.prepend(jump);
    const box=section.querySelector('.progress-history-content'),refresh=section.querySelector('button');let version=0;
    async function loadHistory(){
      const request=++version;refresh.disabled=true;box.innerHTML='<p class="muted">正在加载历史反馈…</p>';
      section.querySelector('.progress-history-count').textContent='';
      try{
        const response=await fetch('/api/progress?task_id='+encodeURIComponent(id)+'&actor_id='+encodeURIComponent(currentActorId()),{cache:'no-store'});
        const records=await response.json().catch(()=>null);
        if(!response.ok||!Array.isArray(records))throw Error(response.status===404?'该任务已移除或当前无权查看。':'历史反馈加载失败，请重试。');
        if(!section.isConnected||request!==version)return;
        section.querySelector('.progress-history-count').textContent=`（${records.length}）`;
        if(!records.length){box.innerHTML='<p class="muted">暂无进度反馈记录。</p>';return;}
        box.innerHTML='<ol class="progress-history-list"></ol><button type="button" class="secondary small progress-history-more">加载更早反馈</button>';
        const list=box.querySelector('ol'),more=box.querySelector('button');let shown=0;
        const next=()=>{const end=Math.min(shown+10,records.length);list.insertAdjacentHTML('beforeend',records.slice(shown,end).map(reportMarkup).join(''));shown=end;more.hidden=shown>=records.length;more.textContent=`加载更早反馈（还有 ${records.length-shown} 条）`;};
        more.addEventListener('click',next);next();
      }catch(error){if(section.isConnected&&request===version)box.innerHTML=`<p role="alert">${escape(error.message||'历史反馈加载失败，请重试。')}</p>`;}
      finally{if(section.isConnected&&request===version)refresh.disabled=false;}
    }
    refresh.addEventListener('click',loadHistory);loadHistory();
  }
  window.addEventListener('load',()=>{
    const style=document.createElement('style');style.textContent=`
      .task-progress-history{margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px;min-width:0}
      .progress-history-row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
      .progress-history-row h4{margin:0}.progress-history-count{color:#64748b;font-weight:400}
      .progress-history-row button,.progress-history-more{margin:0;width:auto}
      .progress-history-jump{width:auto;margin:0 0 14px}
      .progress-history-list{list-style:none;margin:14px 0;padding:0}
      .progress-history-item{border-left:3px solid #dbe7fb;border-bottom:1px solid #e8edf4;margin:0 0 14px;padding:0 0 14px 12px;overflow-wrap:anywhere}
      .progress-history-meta{font-size:12px;color:#64748b;margin:6px 0;line-height:1.6}
      .progress-history-text{white-space:pre-wrap;margin:10px 0;font-size:14px;line-height:1.65}
      .progress-history-status{border-radius:6px;padding:3px 8px;font-size:12px;background:#f1f5f9;color:#52627a}
      .progress-history-status.pending{background:#fff3da;color:#946000}.progress-history-status.approved{background:#e7f7ee;color:#157347}
      .progress-history-status.auto_approved{background:#eaf1ff;color:#245dcc}.progress-history-status.rejected{background:#ffeded;color:#b32e2e}
      .progress-history-track{height:5px;border-radius:4px;background:#e9eff8;margin:9px 0;overflow:hidden}.progress-history-track span{display:block;height:100%;background:#4281df}
      .progress-history-blocked{font-size:13px;line-height:1.6;color:#a6362e;white-space:pre-wrap;margin:8px 0}
    `;document.head.append(style);
    // The legacy page decorates both entry points. Install after those decorators.
    for(const name of ['showTaskDetails','openSimilarTaskDetail']){
      const original=window[name];if(typeof original!=='function')continue;
      window[name]=function(id,...args){const result=original.call(this,id,...args);if(result?.then)return result.then(value=>{mount(id);return value;});mount(id);return result;};
    }
  });
})();

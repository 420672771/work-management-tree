// Reports are loaded only when a task is opened, using the task's existing scope.
(()=>{
  const escape=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const time=value=>{
    if(!value)return '未记录';
    const date=new Date(value);
    return Number.isNaN(date.getTime())?String(value):new Intl.DateTimeFormat('zh-CN',{
      timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false
    }).format(date);
  };
  const statuses={pending:'待确认',approved:'已确认',auto_approved:'自动生效',rejected:'已驳回'};
  function reportMarkup(row){
    const status=row.confirmation_status||row.status,label=statuses[status]||'状态未记录';
    const percent=Number(row.reported_percent),known=row.reported_percent!=null&&Number.isFinite(percent)&&percent>=0&&percent<=100;
    return `<li class="progress-history-item" data-progress-id="${escape(row.id)}">
      <div class="progress-history-head"><strong>${known?escape(percent)+'%':'比例未记录'}</strong><span class="progress-history-status ${Object.hasOwn(statuses,status)?status:''}">${label}</span>${known?`<span class="progress-history-mini-track" role="progressbar" aria-label="本次填报进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}" aria-valuetext="${percent}%，${label}"><span style="width:${percent}%"></span></span>`:''}<span class="progress-history-meta"><time datetime="${escape(row.reported_at||'')}" title="${escape(row.reported_at||'')}">${escape(time(row.reported_at))}</time> · ${escape(row.reporter_name||'提交人未记录')}</span></div>
      <div class="progress-history-body"><p class="progress-history-text">${escape(row.report_text||'未填写进度说明')}</p><button type="button" class="progress-history-toggle" hidden aria-expanded="false">展开</button></div>
      ${row.is_blocked?`<p class="progress-history-blocked"><b>阻塞：</b>${escape(row.block_reason||'未填写原因')}</p>`:''}
      ${row.expected_finish?`<p class="progress-history-meta">预计完成：${escape(String(row.expected_finish).slice(0,10))}</p>`:''}
      ${status==='rejected'&&row.result_reason?`<p class="progress-history-blocked">驳回原因：${escape(row.result_reason)}</p>`:''}
    </li>`;
  }
  let resizeObserver;
  function syncExpansion(section){
    section.querySelectorAll('.progress-history-body').forEach(body=>{
      const text=body.querySelector('p'),button=body.querySelector('button');
      button.hidden=!body.classList.contains('expanded')&&text.scrollHeight<=text.clientHeight+1;
    });
  }
  function mount(id){
    const panel=document.querySelector('#taskDetailContent');
    if(!panel||panel.querySelector('#taskEditForm')||panel.querySelector('[data-history-task="'+Number(id)+'"]'))return;
    const section=document.createElement('section');section.className='task-progress-history';section.dataset.historyTask=String(id);
    section.innerHTML='<div class="progress-history-row"><h4>历史反馈 <span class="progress-history-count"></span></h4><button type="button" class="secondary small progress-history-refresh">刷新记录</button></div><div class="progress-history-content" aria-live="polite"></div>';
    panel.append(section);
    const jump=document.createElement('button');jump.type='button';jump.className='secondary small progress-history-jump';jump.textContent='查看历史反馈';
    jump.addEventListener('click',()=>section.scrollIntoView({block:'start'}));panel.prepend(jump);
    const box=section.querySelector('.progress-history-content'),refresh=section.querySelector('button');let version=0;
    box.addEventListener('click',event=>{
      const button=event.target.closest('.progress-history-toggle');if(!button)return;
      const expanded=button.parentElement.classList.toggle('expanded');
      button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'收起':'展开';
    });
    resizeObserver?.disconnect();
    if(window.ResizeObserver){
      const observer=new ResizeObserver(()=>{if(section.isConnected)syncExpansion(section);else observer.disconnect();});
      observer.observe(section);resizeObserver=observer;
    }
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
        const next=()=>{const end=Math.min(shown+10,records.length);list.insertAdjacentHTML('beforeend',records.slice(shown,end).map(reportMarkup).join(''));shown=end;more.hidden=shown>=records.length;more.textContent=`加载更早反馈（还有 ${records.length-shown} 条）`;requestAnimationFrame(()=>{if(section.isConnected)syncExpansion(section);});};
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
      .progress-history-list{list-style:none;margin:10px 0;padding:0}
      .progress-history-item{border-bottom:1px solid #e8edf4;margin:0 0 8px;padding:0 0 8px;overflow-wrap:anywhere}
      .progress-history-head{display:flex;align-items:center;flex-wrap:wrap;gap:4px 8px;line-height:1.5}
      .progress-history-head strong{font-size:14px;color:#253754}
      .progress-history-mini-track{display:block;flex:1 1 80px;min-width:48px;max-width:240px;height:5px;border-radius:3px;background:#e7eef9;overflow:hidden}
      .progress-history-mini-track>span{display:block;height:100%;border-radius:inherit;background:#4281df}
      #taskDetailDialog .progress-history-head>.progress-history-meta{margin:0 0 0 auto;line-height:1.5}
      .progress-history-meta{font-size:12px;color:#64748b;margin:6px 0;line-height:1.6}
      .progress-history-text{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;white-space:pre-wrap;margin:5px 0 0;font-size:14px;line-height:1.55}
      .progress-history-body.expanded .progress-history-text{-webkit-line-clamp:unset;display:block}
      #taskDetailDialog .progress-history-toggle{width:auto;margin:3px 0 0;padding:0;border:0;background:transparent;color:#245dcc;font-size:12px;box-shadow:none}
      .progress-history-status{border-radius:5px;padding:1px 6px;font-size:12px;background:#f1f5f9;color:#52627a}
      .progress-history-status.pending{background:#fff3da;color:#946000}.progress-history-status.approved{background:#e7f7ee;color:#157347}
      .progress-history-status.auto_approved{background:#eaf1ff;color:#245dcc}.progress-history-status.rejected{background:#ffeded;color:#b32e2e}
      .progress-history-blocked{font-size:13px;line-height:1.5;color:#a6362e;white-space:pre-wrap;margin:5px 0 0}
      @media(max-width:540px){#taskDetailDialog .progress-history-head>.progress-history-meta{flex-basis:100%;margin-left:0}}
    `;document.head.append(style);
    // The legacy page decorates both entry points. Install after those decorators.
    for(const name of ['showTaskDetails','openSimilarTaskDetail']){
      const original=window[name];if(typeof original!=='function')continue;
      window[name]=function(id,...args){const result=original.call(this,id,...args);if(result?.then)return result.then(value=>{mount(id);return value;});mount(id);return result;};
    }
  });
})();

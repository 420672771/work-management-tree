function monthlyTextWidth(text,fontSize=18){
  return Array.from(String(text)).reduce((width,char)=>{
    const factor=char===' '?0.3:/[ilI.,:;!|']/u.test(char)?0.32:/[MW@#%]/.test(char)?0.95:/[\x00-\xff]/.test(char)?0.62:1;
    return width+factor*fontSize;
  },0);
}
function monthlyMapLines(text,width,maxLines=Infinity,fontSize=18){
  const lines=[];let line='',size=0;
  for(const char of String(text)){
    const unit=monthlyTextWidth(char,fontSize);
    if(char==='\n'||(line&&size+unit>width)){
      lines.push(line);line='';size=0;
      if(lines.length===maxLines){lines[maxLines-1]+='…';return lines;}
    }
    if(char!=='\n'){line+=char;size+=unit;}
  }
  lines.push(line);return lines;
}
function layoutMonthlyMap(root,collapsed=new Set()){
  const nodes=[],edges=[],cache=new Map(),gap=18;
  function measure(node,depth){
    const kind=node.kind||(depth===0?'root':depth===1?'section':'detail');
    const fontSize=depth===0?24:depth===1?20:18,lineHeight=Math.ceil(fontSize*1.45);
    const limit=depth===0?[260,340]:depth===1?[160,240]:kind==='group'?[190,290]:kind==='task'?[340,550]:[240,760];
    const padding=depth<=1?36:24;
    const longest=Math.max(0,...String(node.text).split('\n').map(line=>monthlyTextWidth(line,fontSize)));
    const width=Math.max(limit[0],Math.min(limit[1],longest+padding));
    const lines=monthlyMapLines(node.text,width-padding,Infinity,fontSize);
    const own={width,height:lines.length*lineHeight+(depth===0?30:16),lines,fontSize,lineHeight,kind};
    const children=collapsed.has(node.id)?[]:node.children;
    const span=Math.max(own.height,children.reduce((sum,n)=>sum+measure(n,depth+1),0)+Math.max(0,children.length-1)*gap);
    cache.set(node.id,{...own,span});return span;
  }
  measure(root,0);
  function place(node,depth,left,centerY,parent){
    const own=cache.get(node.id),item={...own,node,depth,side:1,x:left+own.width/2,y:centerY,color:'#555'};
    nodes.push(item);if(parent)edges.push({parent,child:item,color:'#666'});
    const children=collapsed.has(node.id)?[]:node.children;
    const total=children.reduce((sum,n)=>sum+cache.get(n.id).span,0)+Math.max(0,children.length-1)*gap;
    let top=centerY-total/2;
    for(const child of children){const span=cache.get(child.id).span;place(child,depth+1,left+own.width+(depth===0?90:64),top+span/2,item);top+=span+gap;}
  }
  place(root,0,0,0,null);
  const left=Math.min(...nodes.map(n=>n.x-n.width/2))-48,right=Math.max(...nodes.map(n=>n.x+n.width/2))+48;
  const top=Math.min(...nodes.map(n=>n.y-n.height/2))-48,bottom=Math.max(...nodes.map(n=>n.y+n.height/2))+48;
  return {nodes,edges,bounds:{x:left,y:top,width:right-left,height:bottom-top}};
}
if(typeof module!=='undefined'&&module.exports)module.exports={layoutMonthlyMap,monthlyMapLines};

(() => {
  if(typeof document==='undefined')return;
  const $=selector=>document.querySelector(selector),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const svgNS='http://www.w3.org/2000/svg';
  let user=null,report=null,active=false,busy=false,scope='mine',selected='root',collapsed=new Set(),zoom=null,layout=null,loadVersion=0,identityId=null,demoMode=false,identityVersion=0;
  const idleWaiters=new Set();
  function finishBusy(){busy=false;for(const done of idleWaiters)done();idleWaiters.clear();}
  function whenIdle(){return busy?new Promise(resolve=>idleWaiters.add(resolve)):Promise.resolve();}
  const style=document.createElement('style');
  style.textContent=`#monthlyPage main{max-width:1680px}#monthlyPage .monthly-tabs{display:flex;gap:8px;margin:16px 0}#monthlyPage .monthly-tabs button{background:#eef3fb;color:#47617e}#monthlyPage .monthly-tabs button.active{background:#245dcc;color:#fff}#monthlyPage .monthly-toolbar{display:flex;align-items:end;gap:10px;flex-wrap:wrap;margin:16px 0}#monthlyPage button{width:auto;margin:0}#monthlyPage input,#monthlyPage select{width:auto;min-width:140px}#monthlyPage label{margin:0}#monthlyPage [hidden]{display:none!important}.monthly-workspace{display:block}.monthly-workspace.editing{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start}.monthly-canvas{border:1px solid #dce5f2;border-radius:10px;overflow:hidden;background:#fff}.monthly-map-tools{padding:10px 12px;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid #e6ecf4;background:#fff}.monthly-canvas svg{display:block;max-width:none;touch-action:pan-x pan-y;cursor:grab}#monthlyMap{height:clamp(520px,74vh,900px);overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.monthly-zoom{align-self:center;min-width:46px;text-align:center;color:#667085;font-size:13px}.monthly-canvas.is-fullscreen{position:fixed;inset:0;z-index:10000;border:0;border-radius:0;background:#fff;display:flex;flex-direction:column}.monthly-canvas.is-fullscreen #monthlyMap{height:auto;min-height:0;flex:1}.monthly-canvas.is-fullscreen .monthly-map-tools{flex-shrink:0}.monthly-workspace.fullscreen .monthly-panel{position:fixed;right:20px;top:90px;z-index:10001;width:min(320px,calc(100vw - 40px));max-height:calc(100dvh - 110px);overflow:auto;box-shadow:0 8px 32px #16304b25}.monthly-node{cursor:pointer;outline:none}.monthly-node:focus-visible>rect{stroke:#008a49;stroke-width:2.5}.monthly-node:focus .monthly-underline,.monthly-node.selected .monthly-underline{stroke:#009f55;stroke-width:2.5}.monthly-node text{font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif;user-select:none}.monthly-node .monthly-toggle{cursor:pointer;font-size:16px}.monthly-panel{padding:16px;border:1px solid #dce5f2;border-radius:10px;background:#fbfdff;min-width:0}.monthly-panel h3{font-size:16px;margin:0 0 12px}.monthly-panel textarea{width:100%;box-sizing:border-box;min-height:160px;font:inherit;font-size:14px;line-height:1.6}.monthly-panel textarea:disabled{color:#52627a;background:#f2f5f9}.monthly-panel .monthly-node-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.monthly-panel .monthly-source{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.7;color:#667085;margin-top:16px}.monthly-help{font-size:12px;color:#7888a0;margin:10px 0}.monthly-list{overflow:auto;margin-top:18px}.monthly-list table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}.monthly-list th,.monthly-list td{padding:10px;border-bottom:1px solid #e5eaf2;text-align:left}.monthly-list th{background:#f7faff;color:#6b7f98}.monthly-meta{display:flex;gap:10px 16px;flex-wrap:wrap;align-items:center;margin:10px 0 16px;color:#667085;font-size:13px}.monthly-login{padding:25px;border:1px solid #d9e5f5;background:#f5f9ff;border-radius:10px}.monthly-error{color:#b42318}.monthly-list button{padding:5px 12px}#monthlyPage .monthly-meta .tag{margin:0}#monthlyPage .monthly-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}@media(max-width:1100px){.monthly-workspace.editing{grid-template-columns:minmax(0,1fr)}.monthly-panel textarea{min-height:100px}}@media(max-width:600px){.monthly-workspace.fullscreen .monthly-panel{top:auto;bottom:12px;right:12px;width:calc(100vw - 24px);max-height:50dvh}#monthlyMap{height:65vh;min-height:420px}.monthly-toolbar>*{max-width:100%}}`;
  document.head.append(style);
  const nav=$('.nav-tabs');if(!nav)return;
  const tab=$('#monthlyTab')||document.createElement('button');if(!tab.id){tab.id='monthlyTab';tab.type='button';tab.textContent='个人月报';nav.insertBefore(tab,$('#orgTab'));}
  const page=document.createElement('section');page.id='monthlyPage';page.className='hidden';
  page.innerHTML=`<main><section class="card"><h2>个人月报</h2><p class="muted">基于本人正式任务生成思维导图，修改自动保存，归档后保留快照。</p><div id="monthlyAuth"></div><div id="monthlyBody" hidden><nav class="monthly-tabs"><button type="button" id="monthlyMine" class="active">我的月报</button><button type="button" id="monthlyTeam" hidden>部门月报</button></nav><div class="monthly-toolbar"><label>报告月份<input type="month" id="monthlyMonth"></label><label id="monthlyEmployeeField" hidden>员工<select id="monthlyEmployee"><option value="">全部员工</option></select></label><button type="button" id="monthlyGenerate">生成 / 打开我的月报</button><button type="button" class="secondary" id="monthlyQuery">查询</button></div><div id="monthlyReport" hidden><h3 id="monthlyTitle"></h3><div class="monthly-meta" id="monthlyMeta"></div><div class="monthly-actions" id="monthlyActions"></div><div class="monthly-workspace"><div class="monthly-canvas"><div class="monthly-map-tools"><button type="button" class="secondary" data-map="in" aria-label="放大导图">放大</button><button type="button" class="secondary" data-map="out" aria-label="缩小导图">缩小</button><span id="monthlyZoom" class="monthly-zoom">100%</span><button type="button" class="secondary" data-map="actual">阅读大小</button><button type="button" class="secondary" data-map="width">适应宽度</button><button type="button" class="secondary" data-map="fit">适应画布</button><button type="button" class="secondary" data-map="expand">全部展开</button><button type="button" class="secondary" data-map="collapse">折叠明细</button><button type="button" class="secondary" data-map="editor" aria-expanded="false">节点详情</button><button type="button" class="secondary" data-map="fullscreen" aria-pressed="false">全屏查看</button></div><div id="monthlyMap"></div></div><aside class="monthly-panel" hidden><h3>节点内容</h3><textarea id="monthlyNodeText" aria-label="节点内容" maxlength="4000"></textarea><p id="monthlyNodeHint" class="monthly-help"></p><div class="monthly-node-actions" id="monthlyNodeActions"><button type="button" class="secondary" data-node-action="add">添加子节点</button><button type="button" class="secondary" data-node-action="remove">删除节点</button><button type="button" class="secondary" data-node-action="up">上移</button><button type="button" class="secondary" data-node-action="down">下移</button></div><div id="monthlySource" class="monthly-source"></div></aside></div><p class="monthly-help">从左向右阅读，文字完整显示；滚动或拖动画布，点击 + / − 展开收起。点击节点查看或编辑，可全屏阅读。归档前会完成最后一次保存。</p></div><div class="monthly-list" id="monthlyList"></div></div><p id="monthlyMessage" role="status" aria-live="polite"></p></section></main>`;
  document.body.append(page);
  const monthNow=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).format(new Date());
  $('#monthlyMonth').value=monthNow();
  function message(text,error=false){$('#monthlyMessage').textContent=text;$('#monthlyMessage').className=error?'monthly-error':'muted';}
  async function api(path='',body){
    const identity=identityId,epoch=identityVersion,url='/api/monthly-reports'+path+(!body&&demoMode?(path.includes('?')?'&':'?')+'actor_id='+identity:'');
    const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(demoMode?{...body,actor_id:identity}:body)}:{cache:'no-store'}),data=await response.json();
    if(identity!==identityId||epoch!==identityVersion){const error=new Error('登录身份已变化，请重新打开月报');error.cancelled=true;throw error;}
    if(!response.ok){const error=new Error(data.message||data.error||'月报操作失败');error.status=response.status;throw error;}return data;
  }
  function saveState(state,error){
    const text=$('#monthlySaveState');if(text){text.textContent=state==='saving'?'保存中…':state==='error'?'保存失败，当前输入已保留':'已自动保存';text.className=state==='error'?'monthly-error':'muted';}
    const retry=$('#monthlyRetry');if(retry)retry.hidden=state!=='error'||error?.status===409;
    const archive=$('#monthlyArchive');if(archive)archive.disabled=busy||state==='error';
    const reload=$('#monthlyReload');if(reload)reload.hidden=state!=='error';
  }
  const autosave=createWeeklyAutosave({save:(id,payload)=>api('/'+id,payload),onState:saveState,canEdit:value=>value.can_edit&&value.status==='draft'});
  function flat(root){const result=[];function visit(node,parent){result.push({node,parent});node.children.forEach(n=>visit(n,node));}visit(root,null);return result;}
  function resetFolds(){collapsed=new Set();if(report)for(const {node} of flat(report.editorial.outline))if((node.kind==='group'&&node.children.length>6)||(node.kind==='task'&&report.snapshot.tasks.length>30))collapsed.add(node.id);}
  function setReport(value){report=value;selected='root';zoom=null;autosave.reset(value);resetFolds();$('#monthlyReport').hidden=!value;if(!value)fullscreen(false);if(value){showEditor(false);$('#monthlyMap').scrollTo(0,0);renderMeta();renderGraph();renderEditor();}}
  function renderMeta(){
    if(!report)return;
    $('#monthlyTitle').textContent=report.snapshot.title;
    $('#monthlyMeta').innerHTML=`<span class="tag">${esc(report.status_label)}</span><span>V${report.version}</span><span>数据截至 ${esc(report.snapshot.data_cutoff.replace('T',' ').slice(0,16))}（北京时间）</span>${report.user_id!==user.id?'<span>管理查看 · 只读</span>':''}`;
    $('#monthlyActions').innerHTML=`${report.can_edit?`<button id="monthlyArchive" type="button" data-action="archive" ${busy?'disabled':''}>归档月报</button><span id="monthlySaveState" class="muted">已自动保存</span><button id="monthlyRetry" type="button" class="secondary" data-action="retry" hidden>重试保存</button><button id="monthlyReload" type="button" class="secondary" data-action="reload" hidden>重新加载</button>`:''}${report.can_revise?'<button type="button" data-action="revise">新建修订稿</button>':''}<button type="button" class="secondary" data-action="svg">导出 SVG 导图</button><button type="button" class="secondary" data-action="text">复制月报文本</button><button type="button" class="secondary" data-action="json">导出导图数据</button>`;
    document.querySelectorAll('#monthlyActions button').forEach(button=>button.disabled=busy);
    if(autosave.error)saveState('error',autosave.error);else if(autosave.dirty)saveState('saving');
  }
  function svgElement(name,attributes={}){const value=document.createElementNS(svgNS,name);for(const [key,item] of Object.entries(attributes))value.setAttribute(key,String(item));return value;}
  function makeSVG(model,folds,interactive){
    const map=layoutMonthlyMap(model,folds),svg=svgElement('svg',{xmlns:svgNS,role:'img','aria-label':report.snapshot.title,viewBox:`${map.bounds.x} ${map.bounds.y} ${map.bounds.width} ${map.bounds.height}`});
    const svgStyle=svgElement('style');svgStyle.textContent="text{font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif}path,rect,circle{vector-effect:non-scaling-stroke}";svg.append(svgStyle);
    svg.append(svgElement('rect',{x:map.bounds.x,y:map.bounds.y,width:map.bounds.width,height:map.bounds.height,fill:'#fff'}));
    const baseline=item=>item.kind==='root'||item.kind==='section'?item.y:item.y+item.height/2;
    for(const edge of map.edges){
      const x1=edge.parent.x+edge.parent.width/2,x2=edge.child.x-edge.child.width/2,middle=(x1+x2)/2,y1=baseline(edge.parent),y2=baseline(edge.child);
      svg.append(svgElement('path',{d:`M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`,fill:'none',stroke:edge.color,'stroke-width':edge.parent.depth===0?3:1.3}));
    }
    for(const item of map.nodes){
      const n=item.node,isRoot=item.kind==='root',boxed=isRoot||item.kind==='section',isSelected=interactive&&selected===n.id;
      const g=svgElement('g',{'data-node':n.id,class:'monthly-node'+(isSelected?' selected':''),tabindex:interactive?0:-1,role:interactive?'button':'group','aria-label':n.text});
      const title=svgElement('title');title.textContent=n.text;g.append(title);
      const left=item.x-item.width/2,top=item.y-item.height/2;
      g.append(svgElement('rect',{x:left,y:top,width:item.width,height:item.height,rx:isRoot?item.height/2:boxed?3:0,fill:isRoot?'#00ad5b':isSelected?'#f0faf4':'#fff',stroke:boxed?(isSelected?'#008a49':'#555'):'none','stroke-width':boxed?1.5:0}));
      if(!boxed)g.append(svgElement('path',{class:'monthly-underline',d:`M ${left} ${top+item.height} H ${left+item.width}`,fill:'none',stroke:isSelected?'#009f55':'#666','stroke-width':isSelected?2.5:1.3}));
      item.lines.forEach((line,index)=>{
        const text=svgElement('text',{x:boxed?item.x:left+12,y:item.y-(item.lines.length-1)*item.lineHeight/2+index*item.lineHeight,'text-anchor':boxed?'middle':'start','dominant-baseline':'central',fill:isRoot?'#fff':'#343434','font-size':item.fontSize,'font-weight':['root','section','group','task'].includes(item.kind)?650:400});
        text.textContent=line;g.append(text);
      });
      if(interactive){
        const select=()=>{selected=n.id;showEditor(true);renderGraph();renderEditor();};
        g.addEventListener('click',select);g.addEventListener('keydown',event=>{if(event.target!==g)return;if(event.key==='Enter'){event.preventDefault();select();}});
      }
      if(n.children.length&&interactive){
        const x=item.x+item.width/2+12,y=baseline(item),toggle=svgElement('g',{'data-toggle':n.id,role:'button','aria-label':(folds.has(n.id)?'展开':'折叠')+' '+n.text,'aria-expanded':String(!folds.has(n.id)),tabindex:0});
        toggle.append(svgElement('circle',{cx:x,cy:y,r:10,fill:'#fff',stroke:'#829087'}));
        const label=svgElement('text',{x,y,'text-anchor':'middle','dominant-baseline':'central',fill:'#64756b','font-size':16,class:'monthly-toggle'});label.textContent=folds.has(n.id)?'+':'−';toggle.append(label);
        const change=event=>{event.stopPropagation();folds.has(n.id)?folds.delete(n.id):folds.add(n.id);renderGraph();};
        toggle.addEventListener('click',change);toggle.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();change(event);}});g.append(toggle);
      }
      svg.append(g);
    }
    return {svg,map};
  }
  function showEditor(open){
    const panel=$('#monthlyPage .monthly-panel');panel.hidden=!open;$('#monthlyPage .monthly-workspace').classList.toggle('editing',open);
    const button=$('[data-map="editor"]');button.textContent=open?'收起详情':'节点详情';button.setAttribute('aria-expanded',String(open));
  }
  function applyZoom(value,anchor){
    if(!layout)return;
    const holder=$('#monthlyMap'),previous=zoom||1;
    anchor=anchor||{x:holder.clientWidth/2,y:holder.clientHeight/2};
    const position={x:(holder.scrollLeft+anchor.x)/previous,y:(holder.scrollTop+anchor.y)/previous};
    zoom=Math.max(.1,Math.min(3,value));const svg=holder.querySelector('svg');
    if(svg){svg.style.width=layout.bounds.width*zoom+'px';svg.style.height=layout.bounds.height*zoom+'px';}
    $('#monthlyZoom').textContent=Math.round(zoom*100)+'%';
    holder.scrollLeft=position.x*zoom-anchor.x;holder.scrollTop=position.y*zoom-anchor.y;
  }
  function renderGraph(){
    if(!report)return;
    const holder=$('#monthlyMap'),position={x:holder.scrollLeft,y:holder.scrollTop},output=makeSVG(report.editorial.outline,collapsed,true);
    layout=output.map;holder.replaceChildren(output.svg);
    applyZoom(zoom||Math.max(.72,Math.min(1,holder.clientWidth/layout.bounds.width)),{x:0,y:0});holder.scrollLeft=position.x;holder.scrollTop=position.y;
  }
  const mapHolder=$('#monthlyMap');let drag=null;
  mapHolder.addEventListener('pointerdown',event=>{
    if(event.pointerType!=='mouse'||event.button!==0||event.target.closest('[data-node]'))return;
    drag={x:event.clientX,y:event.clientY,left:mapHolder.scrollLeft,top:mapHolder.scrollTop};mapHolder.setPointerCapture(event.pointerId);event.preventDefault();
  });
  mapHolder.addEventListener('pointermove',event=>{if(drag){mapHolder.scrollLeft=drag.left-(event.clientX-drag.x);mapHolder.scrollTop=drag.top-(event.clientY-drag.y);}});
  mapHolder.addEventListener('pointerup',()=>drag=null);mapHolder.addEventListener('pointercancel',()=>drag=null);
  mapHolder.addEventListener('wheel',event=>{if(event.ctrlKey||event.metaKey){event.preventDefault();const rect=mapHolder.getBoundingClientRect();applyZoom((zoom||1)*(event.deltaY<0?1.1:1/1.1),{x:event.clientX-rect.left,y:event.clientY-rect.top});}},{passive:false});
  let bodyOverflow=null;
  function fullscreen(open){
    const canvas=$('#monthlyPage .monthly-canvas');
    if(open){if(bodyOverflow===null)bodyOverflow=document.body.style.overflow;document.body.style.overflow='hidden';}
    else if(bodyOverflow!==null){document.body.style.overflow=bodyOverflow;bodyOverflow=null;}
    canvas.classList.toggle('is-fullscreen',open);$('#monthlyPage .monthly-workspace').classList.toggle('fullscreen',open);
    const button=$('[data-map="fullscreen"]');button.textContent=open?'退出全屏':'全屏查看';button.setAttribute('aria-pressed',String(open));
  }
  document.addEventListener('keydown',event=>{if(event.key==='Escape')fullscreen(false);});
  function renderEditor(){
    if(!report)return;const entry=flat(report.editorial.outline).find(x=>x.node.id===selected)||{node:report.editorial.outline,parent:null};selected=entry.node.id;
    const editable=report.can_edit&&!busy,protectedNode=['root','metric'].includes(entry.node.kind);
    $('#monthlyNodeText').value=entry.node.text;$('#monthlyNodeText').disabled=!editable||protectedNode;
    $('#monthlyNodeHint').textContent=!report.can_edit?'当前版本只读。':protectedNode?'姓名、月份与统计节点来自任务快照，不可手工修改。':'修改会自动保存；可调整描述、计划和补充说明。';
    $('#monthlyNodeActions').hidden=!editable;
    for(const button of document.querySelectorAll('#monthlyNodeActions button'))button.disabled=button.dataset.nodeAction==='add'?entry.node.kind==='metric':!entry.parent||['root','metric','section'].includes(entry.node.kind);
    const task=report.snapshot.tasks.find(t=>t.id===entry.node.task_id);
    $('#monthlySource').textContent=task?`来源任务 #${task.id}\n${task.title}\n${task.domain_name} · ${task.priority}\n截止 ${task.due_at.slice(0,10)}\n${task.latest_progress||'暂无进展记录'}`:'';
  }
  async function flush(){const okay=await autosave.flush();if(!okay)message('修改尚未保存成功，请重试保存；发生冲突时可重新加载。',true);return okay;}
  async function list(){
    if(!user)return;const q=new URLSearchParams({scope});if($('#monthlyMonth').value)q.set('month',$('#monthlyMonth').value);if(scope==='all'&&$('#monthlyEmployee').value)q.set('user_id',$('#monthlyEmployee').value);
    const data=await api('?'+q);$('#monthlyTeam').hidden=!data.can_view_all;$('#monthlyEmployeeField').hidden=scope!=='all';$('#monthlyGenerate').hidden=scope!=='mine';
    const chosen=$('#monthlyEmployee').value;$('#monthlyEmployee').innerHTML='<option value="">全部员工</option>'+data.people.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');$('#monthlyEmployee').value=chosen;
    $('#monthlyList').innerHTML=data.items.length?`<table><thead><tr><th>月份</th>${scope==='all'?'<th>员工</th>':''}<th>版本</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${data.items.map(r=>`<tr><td>${esc(r.month)}</td>${scope==='all'?`<td>${esc(r.owner_name)}</td>`:''}<td>V${r.version}</td><td>${r.status==='archived'?'已归档':'草稿'}</td><td>${esc(r.updated_at.replace('T',' ').slice(0,16))}</td><td><button type="button" class="secondary" data-report="${r.id}">查看导图</button></td></tr>`).join('')}</tbody></table>`:'<p class="muted">该查询范围内暂无月报。</p>';
    return data;
  }
  function selectedActor(){return typeof currentActorId==='function'?Number(currentActorId()):null;}
  async function identity(){
    const version=++identityVersion,response=await fetch('/api/session',{cache:'no-store'}),profile=await response.json();
    if(version!==identityVersion)return false;
    demoMode=!!profile.monthly_demo&&!profile.user;
    const chosen=profile.user||(demoMode&&typeof currentUsers!=='undefined'?currentUsers.find(u=>Number(u.id)===selectedActor()):null)||null;
    if(identityId!==chosen?.id){loadVersion++;setReport(null);}
    user=chosen;identityId=user?.id||null;
    $('#monthlyBody').hidden=!user;
    $('#monthlyAuth').innerHTML=demoMode?`<p class="muted">测试环境 · 当前演示身份：${esc(user?.name||'正在加载成员')}。可通过页面顶部切换员工，体验月报全部操作；修改保存在测试数据库中。</p>`:user?'':`<div class="monthly-login"><h3>请先登录本人账号</h3><p>员工月报仅本人、部门负责人和管理 PM 可查看。</p>${profile.mode==='feishu'?'<a href="/auth/feishu/start">飞书扫码登录</a>':'<p class="muted">当前站点未启用月报演示操作，请登录员工账号或启用测试环境演示开关。</p><p><a href="/monthly-report-demo" target="_blank" rel="noopener">查看 5 位虚构员工的月报演示 →</a></p>'}</div>`;
    return !!user;
  }
  async function showIdentityReports(){
    if(await identity()){const data=await list();if(data.items.length)await load(data.items[0].id);}
  }
  async function load(id){if(busy||!await flush())return;const version=++loadVersion;busy=true;try{const value=await api('/'+id);if(version===loadVersion)setReport(value);}finally{finishBusy();if(report){renderMeta();renderEditor();}}}
  async function generate(){if(busy||!await flush())return;busy=true;try{const value=await api('/generate',{month:$('#monthlyMonth').value||monthNow()});setReport(value);await list();message('月报已打开，点击导图节点可编辑。');}finally{finishBusy();if(report){renderMeta();renderEditor();}}}
  function download(name,data,type){const url=URL.createObjectURL(new Blob([data],{type})),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function fail(error){if(error.cancelled)return;message(error.message,true);if(error.status===401){setReport(null);user=null;identity();}}
  $('#monthlyGenerate').onclick=()=>generate().catch(fail);$('#monthlyQuery').onclick=()=>list().catch(fail);
  $('#monthlyMine').onclick=async()=>{if(busy||!await flush())return;scope='mine';$('#monthlyMine').classList.add('active');$('#monthlyTeam').classList.remove('active');setReport(null);list().catch(fail);};
  $('#monthlyTeam').onclick=async()=>{if(busy||!await flush())return;scope='all';$('#monthlyMine').classList.remove('active');$('#monthlyTeam').classList.add('active');setReport(null);list().catch(fail);};
  $('#monthlyList').onclick=event=>{const button=event.target.closest('[data-report]');if(button)load(Number(button.dataset.report)).catch(fail);};
  $('#monthlyNodeText').addEventListener('input',event=>{if(!report?.can_edit||busy)return;const entry=flat(report.editorial.outline).find(x=>x.node.id===selected);if(!entry||['root','metric'].includes(entry.node.kind))return;entry.node.text=event.target.value;autosave.edit();renderGraph();});
  $('#monthlyNodeActions').onclick=event=>{
    const button=event.target.closest('[data-node-action]');if(!button||!report?.can_edit||busy)return;
    const entry=flat(report.editorial.outline).find(x=>x.node.id===selected),action=button.dataset.nodeAction;if(!entry)return;
    if(action==='add'){if(entry.node.kind==='metric')return;const value={id:'custom:'+Date.now().toString(36)+Math.random().toString(36).slice(2),text:'补充内容',kind:'custom',children:[]};entry.node.children.push(value);collapsed.delete(entry.node.id);selected=value.id;}
    else{if(!entry.parent||['root','metric','section'].includes(entry.node.kind))return;const array=entry.parent.children,index=array.indexOf(entry.node);if(action==='remove'){array.splice(index,1);selected=entry.parent.id;}else{const target=index+(action==='up'?-1:1);if(target<0||target>=array.length)return;[array[index],array[target]]=[array[target],array[index]];}}
    autosave.edit();renderGraph();renderEditor();
  };
  $('#monthlyActions').onclick=async event=>{
    const button=event.target.closest('[data-action]');if(!button||!report||busy)return;const action=button.dataset.action;
    try{
      if(action==='retry'){await autosave.flush();return;}
      if(action==='reload'){if(autosave.dirty&&!window.confirm('重新加载将放弃尚未保存的输入，是否继续？'))return;busy=true;const fresh=await api('/'+report.id);setReport(fresh);return;}
      if(!await flush())return;
      if(action==='archive'||action==='revise'){busy=true;renderEditor();const value=await api('/'+report.id+(action==='revise'?'/revise':''),action==='revise'?{}:{action:'archive',edit_revision:report.edit_revision});setReport(value);message(action==='archive'?'月报已归档，本版快照已固定。':'修订稿已建立，原归档版本保持不变。');await list();}
      else if(action==='svg'){const {svg,map}=makeSVG(report.editorial.outline,new Set(),false);svg.setAttribute('width',map.bounds.width);svg.setAttribute('height',map.bounds.height);download(report.snapshot.title+'.svg',new XMLSerializer().serializeToString(svg),'image/svg+xml;charset=utf-8');}
      else if(action==='json'){const data=await api('/'+report.id+'/export');download(report.snapshot.title+'.json',JSON.stringify(data,null,2),'application/json;charset=utf-8');}
      else if(action==='text'){const data=await api('/'+report.id+'/text');try{await navigator.clipboard.writeText(data.text);message('月报文本已复制。');}catch(_){download(report.snapshot.title+'.txt',data.text,'text/plain;charset=utf-8');}}
    }catch(error){fail(error);}finally{finishBusy();if(report){renderMeta();renderEditor();}}
  };
  $('.monthly-map-tools').onclick=event=>{
    const button=event.target.closest('[data-map]');if(!button||!report)return;const action=button.dataset.map,holder=$('#monthlyMap');
    if(action==='expand'){collapsed.clear();renderGraph();}
    else if(action==='collapse'){resetFolds();for(const {node} of flat(report.editorial.outline))if(node.kind==='task'||node.id==='section:overview')collapsed.add(node.id);renderGraph();}
    else if(action==='fit'){applyZoom(Math.min(holder.clientWidth/layout.bounds.width,holder.clientHeight/layout.bounds.height),{x:0,y:0});holder.scrollTo(0,0);}
    else if(action==='width'){applyZoom(holder.clientWidth/layout.bounds.width,{x:0,y:0});holder.scrollLeft=0;}
    else if(action==='actual')applyZoom(1);
    else if(action==='editor')showEditor($('#monthlyPage .monthly-panel').hidden);
    else if(action==='fullscreen')fullscreen(!$('#monthlyPage .monthly-canvas').classList.contains('is-fullscreen'));
    else applyZoom((zoom||1)*(action==='in'?1.25:.8));
  };
  const baseOpen=window.openPage;
  window.openPage=async function(name){
    if(active&&name!=='monthly'){if(busy||!await flush())return;active=false;fullscreen(false);page.classList.add('hidden');}
    if(name!=='monthly')return baseOpen(name);
    if(active)return;
    await baseOpen(name);if($('#weeklyPage')&&!$('#weeklyPage').classList.contains('hidden'))return;
    active=true;page.classList.remove('hidden');document.querySelectorAll('.nav-tabs button').forEach(b=>b.classList.toggle('active',b===tab));
    try{await showIdentityReports();}catch(error){fail(error);}
  };
  tab.onclick=()=>openPage('monthly');window.openPersonalMonthlyReport=tab.onclick;
  window.addEventListener('beforeunload',event=>{if(autosave.dirty){event.preventDefault();event.returnValue='';}});
  const baseSetActor=window.setDemoActor;
  if(baseSetActor)window.setDemoActor=async function(id){
    if(demoMode&&identityId!==null&&Number(id)!==identityId){
      await whenIdle();
      if(!await flush()){const control=$('#demoActor');if(control)control.value=String(identityId);return;}
    }
    return baseSetActor(id);
  };
  window.addEventListener('wmt:identity-changed',()=>{
    const current=typeof sessionUser!=='undefined'&&sessionUser?Number(sessionUser.id):demoMode?selectedActor():null;
    if(active&&current!==identityId){
      identityVersion++;loadVersion++;setReport(null);user=null;identityId=null;scope='mine';
      $('#monthlyMine').classList.add('active');$('#monthlyTeam').classList.remove('active');$('#monthlyEmployee').value='';$('#monthlyList').textContent='';$('#monthlyBody').hidden=true;
      showIdentityReports().catch(fail);
    }
  });
})();

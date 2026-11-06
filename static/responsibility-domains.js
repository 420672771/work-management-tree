(()=>{
  const $=selector=>document.querySelector(selector);
  let data={items:[],parents:[]},epoch=0,identity=0;
  const allowed=()=>['manager','pm'].includes(currentUsers.find(user=>Number(user.id)===Number(currentActorId()))?.role);
  async function api(path='',body){
    const actor=currentActorId(),version=epoch;
    const response=await fetch('/api/domains'+path+(body?'':'?actor_id='+actor),body?{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,actor_id:actor})
    }:{cache:'no-store'});
    const result=await response.json();
    if(version!==epoch||actor!==currentActorId()){const error=new Error('当前身份已变化');error.cancelled=true;throw error;}
    if(!response.ok)throw new Error(result.error||'责任域操作失败');
    return result;
  }
  function message(value,error=false){$('#domainMessage').textContent=value;$('#domainMessage').className=error?'domain-error':'muted';if(error&&$('#domainDialog').open)$('#domainFormMessage').textContent=value;}
  function fail(error){if(!error.cancelled)message(error.message,true);}
  function render(){
    const search=$('#domainSearch').value.trim().toLocaleLowerCase();
    const items=data.items.filter(item=>(item.name+' '+(item.domain_code||'')+' '+(item.boundary||'')).toLocaleLowerCase().includes(search));
    $('#domainList').innerHTML=items.length?`<table><thead><tr><th>责任域</th><th>上级</th><th>负责人</th><th>责任边界</th><th>引用情况</th><th>操作</th></tr></thead><tbody>${items.map(item=>{
      const refs=Object.entries(item.references||{}).map(([key,value])=>key+' '+value).join('、');
      return `<tr><td><b>${esc(item.name)}</b><small>${esc(item.domain_code||'')} ${esc(item.operating_line||'')}</small></td><td>${esc(item.parent_name||'—')}</td><td>${esc(item.owner_name||'未指定')}</td><td class="domain-boundary">${esc(item.boundary||'—')}</td><td>${esc(refs||'未被引用')}</td><td><div class="domain-row-actions"><button type="button" class="secondary small" data-domain-edit="${item.id}">修改</button><button type="button" class="secondary small" data-domain-delete="${item.id}" ${item.can_delete?'':'disabled'} title="${esc(refs?'已被引用，只能修改':'删除未引用的责任域')}">删除</button></div></td></tr>`;
    }).join('')}</tbody></table>`:'<p class="muted">暂无符合条件的责任域，可点击“新增责任域”自定义。</p>';
  }
  async function refresh(){if(!allowed())return;data=await api();render();}
  function open(id){
    if(!allowed())return;
    const item=data.items.find(row=>row.id===id);$('#domainForm').reset();$('#domainFormMessage').textContent='';$('#domainId').value=item?.id||'';
    $('#domainDialogTitle').textContent=item?'修改责任域':'新增责任域';
    $('#domainName').value=item?.name||'';$('#domainCode').value=item?.domain_code||'';
    $('#domainLine').value=item?.operating_line||'';$('#domainBoundary').value=item?.boundary||'';
    $('#domainOwner').innerHTML='<option value="">未指定</option>'+currentUsers.map(user=>`<option value="${user.id}">${esc(user.name)}</option>`).join('');
    $('#domainOwner').value=item?.owner_id||'';
    $('#domainParent').innerHTML=data.parents.map(parent=>`<option value="${parent.id}">${esc(parent.name)}${parent.node_type==='department'?'（一级责任域）':''}</option>`).join('');
    $('#domainParentField').hidden=!!item;
    $('#domainDialog').showModal();$('#domainName').focus();
  }
  window.addEventListener('load',()=>{
    const nav=$('#orgPage .org-subnav');if(!nav)return;
    const tab=document.createElement('button');tab.id='orgDomainsTab';tab.type='button';tab.textContent='责任域维护';nav.insertBefore(tab,$('#orgRoutineTab'));
    const panel=document.createElement('section');panel.id='orgDomains';panel.className='card org-panel hidden';
    panel.innerHTML=`<div class="domain-header"><h2>责任域维护</h2><button id="domainCreate" type="button">新增责任域</button></div><p class="muted">自定义工作责任范围。已被任务、模板或历史记录引用的责任域不能删除，仍可修改；修改后历史数据中的责任域信息同步更新。</p><input id="domainSearch" type="search" placeholder="搜索责任域名称、编码或责任边界" aria-label="搜索责任域"><div id="domainList" class="domain-list"></div><p id="domainMessage" class="muted" role="status" aria-live="polite"></p><dialog id="domainDialog"><form id="domainForm"><h3 id="domainDialogTitle">新增责任域</h3><input id="domainId" type="hidden"><label for="domainName">责任域名称 *</label><input id="domainName" required maxlength="200"><div id="domainParentField"><label for="domainParent">上级节点</label><select id="domainParent"></select></div><label for="domainOwner">负责人</label><select id="domainOwner"></select><label for="domainCode">编码</label><input id="domainCode" maxlength="32" placeholder="例如：DATA"><label for="domainLine">所属条线</label><input id="domainLine" maxlength="100"><label for="domainBoundary">责任边界</label><textarea id="domainBoundary" maxlength="5000" rows="4" placeholder="说明该责任域负责的工作范围"></textarea><p class="muted">保存后，任务、例行模板和历史报表中的责任域信息同步生效。</p><p id="domainFormMessage" class="domain-error" role="alert"></p><div class="task-actions"><button id="domainSave" type="submit">保存</button><button id="domainCancel" type="button" class="secondary">取消</button></div></form></dialog>`;
    $('#orgPage main').append(panel);
    const style=document.createElement('style');style.textContent='.domain-header{display:flex;align-items:center;justify-content:space-between;gap:12px}.domain-header h2{margin:0}.domain-header button,.domain-row-actions button{width:auto;margin:0}.domain-list{overflow:auto;margin-top:14px}.domain-list table{border-collapse:collapse;width:100%;font-size:13px}.domain-list td,.domain-list th{padding:12px;border-bottom:1px solid #e5ebf4;text-align:left;vertical-align:top;min-width:90px}.domain-list th{background:#f6f9fd}.domain-list small{display:block;margin-top:4px;color:#7888a0}.domain-boundary{min-width:180px!important;max-width:350px;white-space:pre-wrap;overflow-wrap:anywhere}.domain-row-actions{display:flex;gap:8px}.domain-error{color:#b42318}#domainSearch{max-width:440px}#domainForm label{display:block;margin-top:10px}#domainForm textarea{width:100%;box-sizing:border-box}#domainForm .task-actions{margin-top:16px}#domainDialog{max-height:calc(100dvh - 40px);overflow:auto}#domainParentField[hidden]{display:none!important}';document.head.append(style);
    const previous=window.openOrgSection;
    window.openOrgSection=async section=>{await previous(section);if(section==='orgDomains')refresh().catch(fail);};
    tab.onclick=()=>window.openOrgSection('orgDomains');
    $('#domainCreate').onclick=()=>open();$('#domainCancel').onclick=()=>$('#domainDialog').close();$('#domainSearch').oninput=render;
    $('#domainList').onclick=async event=>{
      const edit=event.target.closest('[data-domain-edit]');if(edit)return open(Number(edit.dataset.domainEdit));
      const button=event.target.closest('[data-domain-delete]');if(!button||button.disabled)return;
      const item=data.items.find(row=>row.id===Number(button.dataset.domainDelete));if(!item)return;
      try{
        if(!await requestActionConfirmation({title:'删除责任域',itemText:item.name,message:'仅未被引用的责任域可以删除。',confirmLabel:'删除',danger:true}))return;
        await api('/'+item.id,{action:'delete'});await load();await loadOrg(true);await refresh();message('责任域已删除。');
      }catch(error){fail(error);refresh().catch(fail);}
    };
    $('#domainForm').onsubmit=async event=>{
      event.preventDefault();if(!allowed())return;
      const button=$('#domainSave');button.disabled=true;
      try{
        const id=$('#domainId').value,body={name:$('#domainName').value,domain_code:$('#domainCode').value,
          operating_line:$('#domainLine').value,boundary:$('#domainBoundary').value,owner_id:Number($('#domainOwner').value)||null};
        if(!id)body.parent_id=Number($('#domainParent').value);
        await api(id?'/'+id:'',body);$('#domainDialog').close();await load();await loadOrg(true);await refresh();
        message(id?'责任域已修改，历史数据已同步更新。':'责任域已新增，可在创建任务和例行模板时选择。');
      }catch(error){fail(error);}finally{button.disabled=false;}
    };
    function roleChanged(){
      tab.hidden=!allowed();tab.classList.toggle('hidden',!allowed());tab.style.setProperty('display',allowed()?'inline-flex':'none','important');
      if(identity!==currentActorId()||(!allowed()&&!panel.classList.contains('hidden'))){
        identity=currentActorId();epoch++;$('#domainDialog').close();data={items:[],parents:[]};render();
        if(!panel.classList.contains('hidden')){
          if(allowed())refresh().catch(fail);else window.openOrgSection('orgPosition');
        }
      }
    }
    window.addEventListener('wmt:identity-changed',roleChanged);roleChanged();
  });
})();

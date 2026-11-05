(() => {
  const $=s=>document.querySelector(s),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let demoAllowed=false;
  const style=document.createElement('style');style.textContent=`#profilePage main{max-width:1040px}#profilePage .profile-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}#profilePage label{display:block;margin:14px 0 6px}#profilePage input{width:100%}#profilePage button{width:auto}#profilePage .profile-info{display:grid;grid-template-columns:90px 1fr;gap:12px;margin:20px 0}#profilePage .profile-info dt{color:#78879b}#profilePage .profile-info dd{margin:0;overflow-wrap:anywhere}.account-status{font-size:13px;min-height:24px;color:#216848;white-space:pre-wrap}.account-error{color:#b42318}.account-details{color:#62748a;font-size:12px;margin-top:6px}#passwordResetDialog{width:min(460px,calc(100vw - 32px))}#passwordResetDialog label{display:block;margin:14px 0 6px}#passwordResetDialog button{width:auto}@media(max-width:720px){#profilePage .profile-grid{grid-template-columns:1fr}}`;
  document.head.append(style);
  const page=document.createElement('section');page.id='profilePage';page.className='hidden';page.innerHTML=`<main><section class="card"><h2>个人中心</h2><p class="muted">管理自己的账号密码与飞书绑定。</p><div id="profileContent"></div><p id="profileStatus" class="account-status" role="status"></p></section></main>`;document.body.append(page);
  const reset=document.createElement('dialog');reset.id='passwordResetDialog';reset.innerHTML=`<form id="passwordResetForm"><h3>重置员工密码</h3><p id="resetEmployee" class="muted"></p><input id="resetUserId" type="hidden"><label for="resetNewPassword">新密码</label><input id="resetNewPassword" type="password" autocomplete="new-password" minlength="8" maxlength="128" required><label for="resetConfirmPassword">再次输入新密码</label><input id="resetConfirmPassword" type="password" autocomplete="new-password" minlength="8" maxlength="128" required><p class="muted">重置后，该员工原有登录会话会失效。</p><p id="resetStatus" class="account-status" role="status"></p><div class="task-actions"><button id="resetSubmit" type="submit">确认重置</button><button class="secondary" type="button" id="resetCancel">取消</button></div></form>`;document.body.append(reset);
  const managed=()=>{const actor=sessionUser||(demoAllowed?currentUsers.find(u=>Number(u.id)===Number(currentActorId())):null);return actor&&['manager','pm'].includes(actor.role);};
  function status(selector,text,error=false){$(selector).textContent=text;$(selector).className='account-status'+(error?' account-error':'');}
  async function api(path,body){const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'}),value=await response.json();if(!response.ok){const error=new Error(value.error||'操作失败');error.status=response.status;throw error;}return value;}
  const bindingMessages={success:'飞书绑定成功，今后可以直接扫码登录。',already_bound:'该飞书账号已绑定其他员工，请使用本人飞书账号。',different_account:'当前已绑定另一个飞书账号，如需变更请联系负责人或管理 PM。',authorization_failed:'飞书授权未完成，请重新扫码。',cancelled:'已取消飞书绑定。',not_configured:'飞书网页登录尚未完成回调配置。'};
  async function profile(){
    try{
      const data=await api('/api/me'),u=data.user;
      $('#profileContent').innerHTML=`<dl class="profile-info"><dt>姓名</dt><dd>${esc(u.name)}</dd><dt>登录账号</dt><dd>${esc(u.username||'尚未配置，请联系负责人或管理 PM')}</dd><dt>部门</dt><dd>${esc(u.department||'—')}</dd><dt>角色</dt><dd>${esc({manager:'部门负责人',pm:'管理 PM',member:'员工'}[u.role]||u.role)}</dd></dl><div class="profile-grid"><section><h3>修改密码</h3><form id="changePasswordForm"><label for="profileOldPassword">当前密码</label><input id="profileOldPassword" type="password" autocomplete="current-password" required maxlength="128"><label for="profileNewPassword">新密码</label><input id="profileNewPassword" type="password" autocomplete="new-password" required minlength="8" maxlength="128" placeholder="8—128 个字符"><label for="profileConfirmPassword">确认新密码</label><input id="profileConfirmPassword" type="password" autocomplete="new-password" required minlength="8" maxlength="128"><div class="task-actions"><button id="profilePasswordSubmit" type="submit" ${u.username?'':'disabled'}>更新密码</button></div></form></section><section><h3>飞书账号</h3><p>${data.feishu_bound?'已绑定 · '+esc(data.feishu_masked):'尚未绑定'}</p><p class="muted">绑定本人飞书后，可在登录页使用飞书扫码进入网页端。</p>${data.feishu_bound?'<p class="muted">如需更换绑定，请联系部门负责人或管理 PM。</p>':`<button type="button" id="profileBindFeishu" ${data.feishu_ready?'':'disabled'}>飞书扫码绑定</button>`}${!data.feishu_ready?'<p class="muted">飞书网页登录尚未完成回调配置。</p>':''}</section></div>`;
      $('#profileBindFeishu')?.addEventListener('click',()=>location.assign('/auth/feishu/bind'));
      $('#changePasswordForm').onsubmit=async event=>{
        event.preventDefault();if($('#profileNewPassword').value!==$('#profileConfirmPassword').value)return status('#profileStatus','两次输入的新密码不一致',true);
        $('#profilePasswordSubmit').disabled=true;try{const result=await api('/api/me/password',{current_password:$('#profileOldPassword').value,new_password:$('#profileNewPassword').value});$('#changePasswordForm').reset();status('#profileStatus',result.message);}catch(error){status('#profileStatus',error.message,true);}finally{$('#profilePasswordSubmit').disabled=false;}
      };
      const message=new URLSearchParams(location.search).get('binding');if(message)status('#profileStatus',bindingMessages[message]||'请重试扫码绑定',message!=='success');
    }catch(error){if(error.status===401)location.assign('/login');else status('#profileStatus',error.message,true);}
  }
  const baseOpen=window.openPage;
  window.openPage=async function(name){
    await baseOpen(name);
    if(name==='profile'&&(['monthlyPage','weeklyPage'].some(id=>$('#'+id)&&!$('#'+id).classList.contains('hidden'))))return;
    page.classList.toggle('hidden',name!=='profile');if(name==='profile'){document.querySelectorAll('.nav-tabs button').forEach(button=>button.classList.remove('active'));await profile();}
  };
  window.openPersonalCenter=()=>openPage('profile');
  const baseEdit=window.editMember;
  window.editMember=id=>{if(!managed())return;const u=currentUsers.find(u=>Number(u.id)===Number(id));baseEdit(id);$('#memberAccount').value=u?.username||'';$('#memberPassword').value='';$('#memberAccount').required=false;$('#memberPassword').required=false;};
  const baseCreate=window.openMemberDialog;
  window.openMemberDialog=()=>{if(!managed())return;baseCreate();$('#memberRole').value='member';$('#memberAccount').required=true;$('#memberPassword').required=true;};
  const baseRender=window.renderOrgRosterEditable;
  window.renderOrgRosterEditable=async function(...args){
    await baseRender(...args);
    document.querySelectorAll('#userPositions .account-details,#userPositions [data-reset-user]').forEach(node=>node.remove());
    for(const button of document.querySelectorAll('#userPositions [onclick^="editMember("]')){
      const id=Number(button.getAttribute('onclick').match(/\d+/)?.[0]),u=currentUsers.find(u=>Number(u.id)===id),row=button.closest('.org-info-row');if(!u||!row)continue;
      const details=document.createElement('div');details.className='account-details';details.textContent=u.username?'账号：'+u.username:'账号：未配置';row.querySelector('.task-actions').before(details);
      if(managed()){
        const action=document.createElement('button');action.type='button';action.className='secondary';action.textContent='重置密码';action.dataset.resetUser=id;action.disabled=!u.username;action.onclick=()=>openReset(id);row.querySelector('.task-actions').append(action);
      }else row.querySelector('.task-actions').hidden=true;
    }
    document.querySelector('#orgPosition [onclick="openMemberDialog()"]')?.toggleAttribute('disabled',!managed());
  };
  function openReset(id){if(!managed())return;const user=currentUsers.find(u=>Number(u.id)===id);$('#passwordResetForm').reset();$('#resetUserId').value=id;$('#resetEmployee').textContent=`${user.name} · ${user.username}`;status('#resetStatus','');reset.showModal();$('#resetNewPassword').focus();}
  $('#resetCancel').onclick=()=>reset.close();
  $('#passwordResetForm').onsubmit=async event=>{
    event.preventDefault();if($('#resetNewPassword').value!==$('#resetConfirmPassword').value)return status('#resetStatus','两次输入的密码不一致',true);
    $('#resetSubmit').disabled=true;try{const id=Number($('#resetUserId').value),out=await api('/api/users/'+id+'/password-reset',{new_password:$('#resetNewPassword').value,actor_id:currentActorId()});$('#passwordResetForm').reset();reset.close();if(sessionUser?.id===id)return location.assign('/login');alert(out.message);}catch(error){status('#resetStatus',error.message,true);}finally{$('#resetSubmit').disabled=false;}
  };
  let routed=false;
  function routeProfile(){if(!routed&&new URLSearchParams(location.search).get('page')==='profile'){routed=true;openPersonalCenter();}}
  window.addEventListener('wmt:bootstrapped',routeProfile);
  if(typeof sessionUser!=='undefined'&&sessionUser)setTimeout(routeProfile,0);
  api('/api/session').then(state=>{demoAllowed=state.monthly_demo;}).catch(()=>{});
})();

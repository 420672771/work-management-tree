// One entry: always start with the official QR SDK, independent of browser SSO.
(() => {
  let pending=null;
  function status(text,flow='login'){
    const element=document.querySelector(flow==='bind'?'#profileStatus':'#loginStatus')||document.querySelector('#loginHint');
    if(element)element.textContent=text;
  }
  function cleanup(){if(!pending)return;clearTimeout(pending.timer);clearInterval(pending.closedTimer);pending.channel?.close();pending=null;}
  function validTarget(target){
    if(typeof target!=='string')return false;
    if(target==='/')return true;
    return /^\/\?page=profile&binding=(success|already_bound|different_account|authorization_failed|cancelled|not_configured)$/.test(target)||
      /^\/login\?login_error=(expired|unbound|not_configured|authorization_failed|cancelled)$/.test(target);
  }
  async function complete(message){
    const current=pending;
    if(!current||current.completing||message?.type!=='wmt:feishu-complete'||message.channel!==current.key||!validTarget(message.target))return;
    current.completing=true;
    try{
      if(message.target==='/'||message.target==='/?page=profile&binding=success'){
        const response=await fetch('/api/me',{cache:'no-store'}),me=await response.json();
        if(!response.ok||!me.user||(current.flow==='bind'&&!me.feishu_bound))throw new Error('未获取到有效登录结果，请重试。');
      }
      if(pending!==current)return;
      const popup=current.popup;cleanup();try{popup.close();}catch{}
      location.assign(message.target);
    }catch(error){if(pending===current){status(error.message,current.flow);current.completing=false;}}
  }
  window.addEventListener('message',event=>{
    if(pending&&event.origin===location.origin&&event.source===pending.popup)complete(event.data);
  });
  function open(mode='login'){
    const flow=mode==='bind'?'bind':'login';
    if(pending&&!pending.popup.closed){pending.popup.focus();return;}
    cleanup();
    const key=Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
    let channel=null;
    try{if(typeof BroadcastChannel==='function')channel=new BroadcastChannel('wmt-feishu-'+key);}catch{}
    const width=Math.min(640,screen.availWidth),height=Math.min(760,screen.availHeight);
    const popup=window.open('/auth/feishu/window?flow='+flow+'&channel='+key,'wmtFeishuLogin',
      'popup=yes,width='+width+',height='+height+',left='+Math.max(0,Math.round((screen.availWidth-width)/2))+',top='+Math.max(0,Math.round((screen.availHeight-height)/2)));
    if(!popup){channel?.close();status('浏览器阻止了登录窗口，请允许本站弹出窗口后重新点击“飞书扫码登录”。',flow);return;}
    pending={key,flow,popup,channel,completing:false};
    if(channel)channel.onmessage=event=>complete(event.data);
    status('请在飞书窗口使用手机扫码确认，完成后将自动返回。',flow);
    pending.closedTimer=setInterval(()=>{
      // Some identity providers sever window.opener. Keep the same-origin result channel alive.
      if(pending?.key===key&&popup.closed){clearInterval(pending.closedTimer);status('请在飞书窗口完成操作；如已关闭，可重新点击“飞书扫码登录”。',flow);}
    },700);
    pending.timer=setTimeout(()=>{if(pending?.key===key){cleanup();status('飞书登录已超时，请重新点击“飞书扫码登录”。',flow);}},600000);
    popup.focus();
  }
  window.openFeishuQr=open;
  window.addEventListener('pagehide',cleanup);
})();

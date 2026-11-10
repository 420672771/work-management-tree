// Official QR SDK: https://open.feishu.cn/document/common-capabilities/sso/web-application-sso/qr-sdk-documentation
(() => {
  const sdkUrl='https://lf-package-cn.feishucdn.com/obj/feishu-static/lark/passport/qrcode/LarkSSOSDKWebQRCode-1.0.3.js';
  let sdkPromise,requestQueue=Promise.resolve(),generation=0,active=null,expiryTimer;
  const style=document.createElement('style');
  style.textContent=`#feishuQrDialog{box-sizing:border-box;width:min(380px,calc(100vw - 24px));max-height:calc(100dvh - 24px);padding:24px 16px;border:1px solid #dce5f2;border-radius:16px;background:#fff;color:#203954;text-align:center;font:14px/1.6 -apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif;overflow:auto}#feishuQrDialog::backdrop{background:#132c4c70}#feishuQrDialog h2{font-size:21px;margin:0 28px 8px}#feishuQrDialog p{margin:8px 0}#feishuQrDialog .wmt-qr-close{position:absolute;right:12px;top:10px;width:32px;height:32px;padding:0;border:0;background:transparent;color:#53677e;font-size:24px;cursor:pointer}#feishuQrDialog .wmt-qr-hint{font-size:13px;color:#6c7d92}#feishuQrContainer{width:300px;max-width:100%;min-height:40px;margin:8px auto}#feishuQrContainer iframe{display:block;max-width:100%;border:0}#feishuQrDialog .wmt-qr-actions{display:flex;justify-content:center;gap:12px;margin-top:16px}#feishuQrDialog .wmt-qr-actions button{width:auto;margin:0;padding:8px 16px;border:1px solid #dce5f2;border-radius:8px;background:#f2f6fc;color:#315685;font:inherit;cursor:pointer}#feishuQrDialog button:disabled{opacity:.5;cursor:default}#feishuQrDialog a{color:#245dcc}#feishuQrStatus{min-height:23px}#feishuQrStatus[data-error=true]{color:#b42318}#feishuQrDialog [hidden]{display:none!important}`;
  document.head.append(style);
  const dialog=document.createElement('dialog');dialog.id='feishuQrDialog';
  dialog.setAttribute('aria-labelledby','feishuQrTitle');
  dialog.innerHTML='<button type="button" class="wmt-qr-close" aria-label="关闭扫码窗口">×</button><h2 id="feishuQrTitle">飞书扫码登录</h2><p class="wmt-qr-hint">打开手机飞书，使用“扫一扫”扫描下方二维码。</p><div id="feishuQrContainer"></div><p id="feishuQrStatus" role="status" aria-live="polite"></p><p class="wmt-qr-hint">请使用本人工作飞书，在手机上确认。<br>如飞书另行要求授权，请核对账号后继续。</p><div class="wmt-qr-actions"><button id="feishuQrRefresh" type="button">刷新二维码</button><button id="feishuQrCancel" type="button">取消</button></div><p class="wmt-qr-hint"><a id="feishuQrFallback" hidden>在手机或飞书授权页继续</a></p>';
  document.body.append(dialog);
  const container=dialog.querySelector('#feishuQrContainer'),status=dialog.querySelector('#feishuQrStatus'),refresh=dialog.querySelector('#feishuQrRefresh'),fallback=dialog.querySelector('#feishuQrFallback');
  let flow='login';
  function message(text,error=false){status.textContent=text;status.dataset.error=String(error);}
  function clear(){active=null;clearTimeout(expiryTimer);container.replaceChildren();fallback.hidden=true;}
  function loadSdk(){
    if(typeof window.QRLogin==='function')return Promise.resolve();
    if(sdkPromise)return sdkPromise;
    sdkPromise=new Promise((resolve,reject)=>{
      const script=document.createElement('script');script.src=sdkUrl;script.async=true;
      const timer=setTimeout(()=>finish(new Error('二维码组件加载超时，请检查网络后刷新二维码。')),15000);
      function finish(error){clearTimeout(timer);script.onload=script.onerror=null;if(error){script.remove();sdkPromise=null;reject(error);}else resolve();}
      script.onload=()=>finish(typeof window.QRLogin==='function'?null:new Error('二维码组件不可用，请刷新重试。'));
      script.onerror=()=>finish(new Error('二维码组件加载失败，请检查网络后刷新二维码。'));
      document.head.append(script);
    });
    return sdkPromise;
  }
  async function showCode(){
    const attempt=++generation;clear();refresh.disabled=true;message('正在加载二维码…');
    // Serialize state creation, including when a window is closed and reopened mid-request.
    const requestedFlow=flow;
    const request=requestQueue.catch(()=>{}).then(async()=>{
      if(attempt!==generation)return null;
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      try{
        const response=await fetch('/auth/feishu/qr?flow='+requestedFlow,{cache:'no-store',credentials:'same-origin',signal:controller.signal});
        const data=await response.json();if(!response.ok)throw new Error(data.error||'无法获取二维码，请重试。');
        return {...data,receivedAt:Date.now()};
      }finally{clearTimeout(timer);}
    });
    requestQueue=request;
    try{
      const data=await request;if(attempt!==generation||!dialog.open||!data)return;
      const goto=new URL(data.authorization_url);
      if(goto.origin!=='https://accounts.feishu.cn'||goto.pathname!=='/open-apis/authen/v1/authorize')throw new Error('飞书登录地址无效，请联系运维。');
      fallback.href=goto.href;fallback.hidden=false;
      await loadSdk();if(attempt!==generation||!dialog.open)return;
      const sdk=window.QRLogin({id:container.id,goto:goto.href,width:'300',height:'340',style:'border:0;display:block;'});
      const frame=container.querySelector('iframe');if(!frame)throw new Error('无法显示二维码，请刷新重试。');
      frame.title='使用手机飞书扫码';
      active={sdk,frame,goto};message('请使用手机飞书扫码并确认。');
      const remaining=Number(data.expires_in)*1000-(Date.now()-data.receivedAt);
      expiryTimer=setTimeout(()=>{active=null;container.replaceChildren();fallback.hidden=true;message('二维码已过期，请点击“刷新二维码”。',true);},Math.max(0,remaining));
    }catch(error){if(attempt===generation&&dialog.open)message(error.name==='AbortError'?'获取二维码超时，请刷新重试。':error.message,true);}
    finally{if(attempt===generation)refresh.disabled=false;}
  }
  window.addEventListener('message',event=>{
    const current=active;
    // The SDK's host matcher alone is insufficient: also check exact origin and iframe window.
    if(!dialog.open||!current||event.origin!==current.goto.origin||event.source!==current.frame.contentWindow)return;
    if(!current.sdk.matchOrigin(event.origin)||!current.sdk.matchData(event.data)||typeof event.data.tmp_code!=='string'||event.data.tmp_code.length>4096)return;
    const target=new URL(current.goto.href);target.searchParams.set('tmp_code',event.data.tmp_code);
    active=null;clearTimeout(expiryTimer);refresh.disabled=true;message('扫码已确认，正在完成飞书授权…');
    window.location.assign(target.href);
  });
  dialog.querySelector('.wmt-qr-close').onclick=()=>dialog.close();
  dialog.querySelector('#feishuQrCancel').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{generation++;clear();refresh.disabled=false;});
  refresh.onclick=showCode;
  window.openFeishuQr=(mode='login')=>{
    if(dialog.open)return;
    flow=mode==='bind'?'bind':'login';dialog.querySelector('#feishuQrTitle').textContent=flow==='bind'?'飞书扫码绑定':'飞书扫码登录';
    dialog.showModal();showCode();
  };
})();

// Run with isolated fixture token/userinfo responses; never authorize a real account in this test.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_TEST_URL;
assert(base?.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch();
 try{
  const errors=[];
  async function fixture(loggedIn=false,extra={}){
   const context=await browser.newContext({viewport:{width:1250,height:1000},...extra});
   if(loggedIn)await context.addCookies([{name:'fixture_feishu_session',value:'recognized',domain:'accounts.feishu.cn',path:'/',secure:true}]);
   // Routing only matches the first URL in an HTTP redirect chain. Preserve the server's
   // real state/cookie response, then navigate separately to the isolated provider fixture.
   await context.route(base+'/auth/feishu/window?**',async route=>{
    const response=await route.fetch({maxRedirects:0}),headers=response.headers();
    assert.equal(response.status(),302);const target=headers.location;
    delete headers['content-length'];delete headers.location;
    await route.fulfill({response,status:200,headers:{...headers,'content-type':'text/html; charset=utf-8'},body:'<script>location.replace('+JSON.stringify(target)+')</script>'});
   });
   await context.route('https://accounts.feishu.cn/open-apis/authen/v1/authorize?**',async route=>{
    const loggedIn=(await route.request().allHeaders()).cookie?.includes('fixture_feishu_session=recognized');
    await route.fulfill({contentType:'text/html; charset=utf-8',headers:loggedIn?{'Cross-Origin-Opener-Policy':'same-origin'}:{},body:loggedIn?'<h1>确认授权</h1><button>授权</button>':'<h1>手机飞书扫码登录</h1><canvas width="234" height="234"></canvas>'});
   });
   const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));return {page,context};
  }
  async function open(page,selector='#feishuLogin'){
   const requestPromise=page.context().waitForEvent('request',r=>r.url().startsWith(base+'/auth/feishu/window?'));
   const popupPromise=page.waitForEvent('popup');await page.locator(selector).click();const popup=await popupPromise;
   const key=new URL((await requestPromise).url()).searchParams.get('channel');
   await popup.waitForURL('https://accounts.feishu.cn/open-apis/authen/v1/authorize?**');
   return {popup,key,state:new URL(popup.url()).searchParams.get('state')};
  }
  for(const loggedIn of [false,true]){
   const {page,context}=await fixture(loggedIn);await page.goto(base+'/login');await page.locator('#feishuLogin:not([disabled])').waitFor();
   assert.equal(await page.getByRole('button',{name:'飞书扫码登录',exact:true}).count(),1);
   assert.equal(await page.getByRole('button',{name:'使用已登录的飞书账号授权',exact:true}).count(),0);
   const {popup,key,state}=await open(page);
   await popup.getByRole('heading',{name:loggedIn?'确认授权':'手机飞书扫码登录'}).waitFor();
   assert.equal(new URL(page.url()).pathname,'/login');assert.equal(/^[a-f0-9]{32}$/.test(key),true);
   // Wrong origin/source, stale attempt, and an external navigation target cannot finish login.
   await page.evaluate(key=>{
    for(const data of [{channel:key,target:'https://evil.example'},{channel:'b'.repeat(32),target:'/'}]){
     window.dispatchEvent(new MessageEvent('message',{origin:location.origin,source:window,data:{type:'wmt:feishu-complete',...data}}));
    }
    const channel=new BroadcastChannel('wmt-feishu-'+key);channel.postMessage({type:'wmt:feishu-complete',channel:key,target:'https://evil.example'});channel.close();
   },key);
   assert.equal(new URL(page.url()).pathname,'/login');
   await popup.goto(base+'/auth/feishu/callback?'+new URLSearchParams({state,code:'bound-code'})).catch(error=>{if(!popup.isClosed())throw error;});
   await page.waitForURL(base+'/');assert.equal((await (await page.request.get(base+'/api/me')).json()).feishu_bound,true);
   await context.close();
  }
  // Unbound identities and user rejection return to the same login page with a useful error.
  for(const code of ['unbound-code',null]){
   const {page,context}=await fixture();await page.goto(base+'/login');await page.locator('#feishuLogin:not([disabled])').waitFor();
   const {popup,state}=await open(page);const query=new URLSearchParams({state});if(code)query.set('code',code);
   await popup.goto(base+'/auth/feishu/callback?'+query).catch(error=>{if(!popup.isClosed())throw error;});
   await page.waitForURL(base+'/login?login_error='+(code?'unbound':'cancelled'));
   assert.equal((await page.request.get(base+'/api/me')).status(),401);await context.close();
  }
  // Binding keeps the existing employee session and returns to that employee's personal center.
  const {page:admin,context:adminContext}=await fixture();await admin.goto(base+'/login');
  await admin.locator('#loginName').fill('admin.browser');await admin.locator('#loginPassword').fill('AdminBrowser2026!');await admin.locator('#loginSubmit').click();await admin.waitForURL(base+'/');
  await admin.getByRole('button',{name:'个人中心',exact:true}).click();await admin.locator('#profileBindFeishu').waitFor();
  const binding=await open(admin,'#profileBindFeishu');
  await binding.popup.goto(base+'/auth/feishu/callback?'+new URLSearchParams({state:binding.state,code:'bind-code'})).catch(error=>{if(!binding.popup.isClosed())throw error;});
  await admin.waitForURL(base+'/?page=profile&binding=success');await admin.waitForFunction(()=>document.querySelector('#profileStatus')?.textContent.includes('绑定成功'));
  assert.equal((await (await admin.request.get(base+'/api/me')).json()).user.username,'admin.browser');await adminContext.close();
  // Popup blocking is visible; closing and retrying creates a fresh attempt. Mobile has one button, too.
  const {page:mobile,context:mobileContext}=await fixture(false,{viewport:{width:375,height:812}});await mobile.goto(base+'/login');await mobile.locator('#feishuLogin:not([disabled])').waitFor();
  await mobile.evaluate(()=>{window.savedOpen=window.open;window.open=()=>null;});await mobile.locator('#feishuLogin').click();
  assert((await mobile.locator('#loginStatus').textContent()).includes('阻止'));assert.equal(new URL(mobile.url()).pathname,'/login');
  await mobile.evaluate(()=>window.open=window.savedOpen);const first=await open(mobile);await first.popup.close();const second=await open(mobile);assert.notEqual(first.state,second.state);
  await second.popup.close();assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await mobile.goto(base+'/auth/feishu/start');assert.equal(await mobile.getByRole('button',{name:'飞书扫码登录',exact:true}).count(),1);
  const entry=await open(mobile,'#entryScan');await entry.popup.getByRole('heading',{name:'手机飞书扫码登录'}).waitFor();await mobileContext.close();
  assert.deepEqual(errors,[]);
  console.log('PASS: one Feishu button, provider-selected QR or consent, popup callback/session and binding return, denied/unbound flows, message validation, popup blocking/retry, mobile and old entry');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

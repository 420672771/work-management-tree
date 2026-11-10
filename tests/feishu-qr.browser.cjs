// Use an isolated account-mode fixture, never a production database.
// FEISHU_APP_ID/SECRET and callback must be configured; admin.browser is a synthetic account.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.WMT_TEST_URL||'http://127.0.0.1:5125';
const sdkPattern='https://lf-package-cn.feishucdn.com/**/LarkSSOSDKWebQRCode-1.0.3.js';
const sdkMock=`window.QRLogin=({id,goto,width,height})=>{const f=document.createElement('iframe');f.src='https://passport.feishu.cn/suite/passport/sso/qr?goto='+encodeURIComponent(goto);f.width=width;f.height=height;document.getElementById(id).append(f);return {matchOrigin:o=>o.startsWith('https://passport.feishu.cn'),matchData:d=>d?.source==='qrcode'&&!!d.tmp_code};};`;
const sdk=process.env.WMT_FEISHU_SDK_FILE?fs.readFileSync(process.env.WMT_FEISHU_SDK_FILE,'utf8'):sdkMock;
(async()=>{
  const browser=await chromium.launch();
  try{
    const context=await browser.newContext({viewport:{width:1200,height:1000}}),page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await context.route(sdkPattern,route=>route.fulfill({contentType:'application/javascript',body:sdk}));
    await context.route('https://passport.feishu.cn/suite/passport/sso/qr**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><p>Isolated QR provider fixture</p>'}));
    // Old bookmarks and login_url responses must reach a QR page, even with old frontend code.
    await page.goto(base+'/auth/feishu/start');await page.locator('#feishuQrContainer iframe').waitFor();
    assert.equal(new URL(page.url()).pathname,'/auth/feishu/start');
    assert.equal(await page.locator('#feishuQrTitle').textContent(),'飞书扫码登录');
    await page.goto(base+'/login');await page.locator('#feishuLogin:not([disabled])').waitFor();
    await page.locator('#feishuLogin').click();await page.locator('#feishuQrContainer iframe').waitFor();
    assert.equal(new URL(page.url()).pathname,'/login');
    assert(await page.locator('#feishuQrDialog').evaluate(dialog=>dialog.open));
    let first=new URL(await page.locator('#feishuQrContainer iframe').getAttribute('src')).searchParams.get('goto');
    assert.equal(new URL(first).searchParams.get('client_id'),'cli_test');
    const cookie=(await context.cookies()).find(c=>c.name==='wmt_oauth_state');assert(cookie.httpOnly);assert.equal(cookie.path,'/auth/feishu');
    // Even an SDK accepting prefix matches must not permit hostile host suffixes or other windows.
    await page.evaluate(()=>{
      const source=document.querySelector('#feishuQrContainer iframe').contentWindow;
      for(const options of [
        {origin:'https://passport.feishu.cn.evil.example',source},
        {origin:'https://passport.feishu.cn',source:window},
        {origin:'https://passport.feishu.cn',source,data:{source:'other',tmp_code:'forged'}},
        {origin:'https://passport.feishu.cn',source,data:{source:'qrcode',tmp_code:{bad:true}}},
      ])window.dispatchEvent(new MessageEvent('message',{data:{source:'qrcode',tmp_code:'forged'},...options}));
    });
    assert.equal(new URL(page.url()).pathname,'/login');
    assert(await page.locator('#feishuQrStatus').textContent().then(s=>s.includes('请使用手机飞书')));
    await page.locator('#feishuQrRefresh').click();
    await page.waitForFunction(first=>{
      const frame=document.querySelector('#feishuQrContainer iframe');return frame&&new URL(frame.src).searchParams.get('goto')!==first;
    },first);
    const refreshed=new URL(await page.locator('#feishuQrContainer iframe').getAttribute('src')).searchParams.get('goto');
    assert.notEqual(new URL(refreshed).searchParams.get('state'),new URL(first).searchParams.get('state'));
    await page.keyboard.press('Escape');await page.locator('#feishuQrDialog').waitFor({state:'hidden'});
    await page.waitForFunction(()=>!document.querySelector('#feishuQrContainer iframe'));
    assert.equal(await page.locator('#feishuQrContainer iframe').count(),0);
    await page.locator('#feishuLogin').click();await page.locator('#feishuQrContainer iframe').waitFor();
    const targetPromise=page.waitForRequest(request=>request.url().startsWith('https://passport.feishu.cn/suite/passport/oauth/authorize?'));
    await context.route('https://accounts.feishu.cn/open-apis/authen/v1/authorize?**',route=>route.fulfill({contentType:'text/html',body:'Authorization handoff fixture'}));
    await page.frameLocator('#feishuQrContainer iframe').locator('p').waitFor();
    await context.route('https://passport.feishu.cn/suite/passport/oauth/authorize?**',route=>route.fulfill({contentType:'text/html',body:'QR authorization handoff fixture'}));
    const qrFrame=page.frames().find(frame=>frame.url().includes('/suite/passport/sso/qr'));
    await qrFrame.evaluate(()=>parent.postMessage({source:'qrcode',tmp_code:'verified+code&safe'},'http://'+new URL(document.referrer).host));
    const target=new URL((await targetPromise).url());assert.equal(target.searchParams.get('tmp_code'),'verified+code&safe');
    assert.equal(target.searchParams.get('state'),(await context.cookies(base+'/auth/feishu/callback')).find(c=>c.name==='wmt_oauth_state').value);
    await page.waitForURL('https://passport.feishu.cn/suite/passport/oauth/authorize?**');

    // Profile binding uses a real logged-in session, the same modal, and a distinct bind flow.
    await page.goto(base+'/login');await page.locator('#loginName').fill('admin.browser');await page.locator('#loginPassword').fill('AdminBrowser2026!');await page.locator('#loginSubmit').click();await page.waitForURL(base+'/');
    await page.getByRole('button',{name:'个人中心',exact:true}).click();await page.locator('#profileBindFeishu').waitFor();
    const binding=page.waitForResponse(response=>response.url().includes('/auth/feishu/qr?flow=bind'));
    await page.locator('#profileBindFeishu').click();assert.equal((await binding).status(),200);
    await page.locator('#feishuQrContainer iframe').waitFor();assert.equal(await page.locator('#feishuQrTitle').textContent(),'飞书扫码绑定');
    await page.locator('#feishuQrCancel').click();await page.waitForFunction(()=>!document.querySelector('#feishuQrContainer iframe'));
    await page.goto(base+'/auth/feishu/bind');await page.locator('#feishuQrContainer iframe').waitFor();
    assert.equal(new URL(page.url()).pathname,'/auth/feishu/bind');
    assert.equal(await page.locator('#feishuQrTitle').textContent(),'飞书扫码绑定');
    await page.locator('#feishuQrCancel').click();await page.locator('#entryAuthorize').click();
    await page.waitForURL('https://accounts.feishu.cn/open-apis/authen/v1/authorize?**');
    assert.equal(new URL(page.url()).searchParams.has('tmp_code'),false);
    await page.goto(base+'/login');await page.locator('#feishuAuthorize:not([disabled])').waitFor();await page.locator('#feishuAuthorize').click();
    await page.waitForURL('https://accounts.feishu.cn/open-apis/authen/v1/authorize?**');

    // The QR window fits a phone; script failure gives a retry and a deliberate authorization link.
    const mobile=await context.newPage({viewport:{width:375,height:812}});await mobile.clock.install();await mobile.goto(base+'/login');
    await mobile.route(sdkPattern,route=>route.abort());await mobile.locator('#feishuLogin').click();
    await mobile.waitForFunction(()=>document.querySelector('#feishuQrStatus').textContent.includes('加载失败'));
    assert.equal(await mobile.locator('#feishuQrFallback').isVisible(),true);
    await mobile.unroute(sdkPattern);await mobile.locator('#feishuQrRefresh').click();await mobile.locator('#feishuQrContainer iframe').waitFor();
    assert(await mobile.evaluate(()=>{const r=document.querySelector('#feishuQrDialog').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&document.documentElement.scrollWidth<=innerWidth;}));
    await mobile.clock.fastForward(600001);
    assert((await mobile.locator('#feishuQrStatus').textContent()).includes('已过期'));assert.equal(await mobile.locator('#feishuQrContainer iframe').count(),0);
    await mobile.locator('.wmt-qr-close').click();assert.equal(await mobile.locator('#feishuQrDialog').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: QR modal, refresh/state rotation, message origin/source/data validation, authorization handoff, profile binding, cancel/reopen, SDK retry, mobile layout, expiry');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

// Opt-in integration check. Use a locally running test service with real Feishu app configuration.
// This opens a QR code without scanning it, authorizing an account, or completing login.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_LIVE_FEISHU_URL;
assert(base&&['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Set WMT_LIVE_FEISHU_URL to a local test service');
(async()=>{
  const browser=await chromium.launch();
  try{
    const context=await browser.newContext({ignoreHTTPSErrors:process.env.WMT_FEISHU_IGNORE_HTTPS_ERRORS==='1'});
    const page=await context.newPage();
    await page.goto(base+'/auth/feishu/start');
    assert.equal(new URL(page.url()).origin,new URL(base).origin);
    const element=page.locator('#feishuQrContainer iframe');await element.waitFor({timeout:25000});
    const frame=await element.contentFrame();
    const image=frame.locator('canvas:visible,img:visible').first();await image.waitFor({timeout:25000});
    await page.waitForFunction(()=>document.querySelector('#feishuQrStatus')?.textContent.includes('请使用手机飞书'));
    await image.evaluate(img=>img.tagName!=='IMG'||img.complete?Promise.resolve():new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;}));
    assert(await image.evaluate(img=>img.tagName==='CANVAS'?img.width>=200&&img.height>=200:img.naturalWidth>=200&&img.naturalHeight>=200),'Expected a real QR image');
    assert(!(await frame.locator('body').innerText()).includes('4401'));
    assert.equal(new URL(page.url()).pathname,'/auth/feishu/start');
    if(process.env.WMT_FEISHU_QR_SCREENSHOT)await page.screenshot({path:process.env.WMT_FEISHU_QR_SCREENSHOT});
    console.log('PASS: fresh browser, legacy entry stays on site, official SDK and real Feishu QR render without response mocks; phone authorization not performed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

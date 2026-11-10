// Opt-in: loads the real official QR SDK without scanning or granting authorization.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_LIVE_FEISHU_URL;
assert(base&&['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Set WMT_LIVE_FEISHU_URL to a local test service');
(async()=>{
 const browser=await chromium.launch();
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:process.env.WMT_FEISHU_IGNORE_HTTPS_ERRORS==='1'}),page=await context.newPage();
  await page.goto(base+'/login');await page.locator('#feishuLogin:not([disabled])').waitFor();
  assert.equal(await page.getByRole('button',{name:'飞书扫码登录',exact:true}).count(),1);
  assert.equal(await page.locator('#feishuAuthorize').count(),0);
  const opened=page.waitForEvent('popup');await page.locator('#feishuLogin').click();const popup=await opened;
  await popup.waitForURL(base+'/auth/feishu/window?**');
  const frame=popup.frameLocator('#qrContainer iframe'),image=frame.locator('canvas:visible,img:visible').first();
  await image.waitFor({timeout:30000});
  await image.evaluate(img=>img.tagName!=='IMG'||img.complete?Promise.resolve():new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;}));
  assert(await image.evaluate(img=>img.tagName==='CANVAS'?img.width>=200&&img.height>=200:img.naturalWidth>=200&&img.naturalHeight>=200),'Expected real QR content');
  assert(!(await frame.locator('body').innerText()).includes('4401'));
  assert.equal(new URL(page.url()).pathname,'/login');assert.equal(new URL(popup.url()).origin,new URL(base).origin);
  if(process.env.WMT_FEISHU_QR_SCREENSHOT)await popup.screenshot({path:process.env.WMT_FEISHU_QR_SCREENSHOT});
  console.log('PASS: one Feishu button always opens a QR page, actual official SDK and real QR rendered, no direct consent navigation; phone authorization not performed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

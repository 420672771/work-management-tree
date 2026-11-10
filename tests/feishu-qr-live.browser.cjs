// Opt-in: opens the real provider from a fresh browser, without scanning or granting consent.
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
  await popup.waitForURL(url=>url.protocol==='https:'&&/(^|\.)feishu\.cn$/.test(url.hostname),{timeout:30000});
  await popup.getByText(/Log In With QR Code|扫码登录|二维码登录/).first().waitFor({timeout:30000});
  assert.equal(new URL(page.url()).pathname,'/login');
  if(process.env.WMT_FEISHU_QR_SCREENSHOT)await popup.screenshot({path:process.env.WMT_FEISHU_QR_SCREENSHOT});
  console.log('PASS: one Feishu button, fresh browser, real official popup chooses QR login; no Feishu responses mocked and no phone authorization performed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

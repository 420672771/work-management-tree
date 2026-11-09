// Run only against an isolated account-mode fixture, never the production site.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_MANUAL_TEST_URL;
assert(base?.startsWith('http://127.0.0.1:'),'Set WMT_MANUAL_TEST_URL to an isolated local fixture');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  assert.equal((await page.request.get(base+'/manual.md')).status(),401);
  await page.goto(base+'/login');await page.locator('#loginName').fill(process.env.WMT_MANUAL_TEST_USERNAME||'manualreader');
  await page.locator('#loginPassword').fill(process.env.WMT_MANUAL_TEST_PASSWORD||'123456');
  await page.locator('#loginSubmit').click();await page.waitForURL(base+'/');await page.goto(base+'/manual');
  await page.waitForFunction(()=>document.querySelectorAll('#manual .manual-figure').length===23);
  await page.waitForFunction(()=>[...document.querySelectorAll('#manual img')].every(img=>img.complete&&img.naturalWidth>0));
  assert.equal(await page.locator('#manualToc a').count(),19);
  assert.equal(await page.locator('#manual img[src$=".png"]').count(),16);
  assert.equal(await page.locator('#manual img[src$=".svg"]').count(),7);
  const images=await page.locator('#manual img').evaluateAll(items=>items.map(item=>({src:item.getAttribute('src'),alt:item.alt})));
  for(const [index,image] of images.entries()){
   assert(image.alt.startsWith('图 '+(index+1)+'　'),image.alt);
   const response=await page.request.get(base+image.src);assert.equal(response.status(),200,image.src);
   assert(response.headers()['content-type'].startsWith(image.src.endsWith('.png')?'image/png':'image/svg+xml'));
  }
  assert.equal((await page.request.get(base+'/static/manual-assets/not-a-real-image.png')).status(),404);
  assert.equal((await page.request.get(base+'/static/manual-assets/%2e%2e%2fapp.py')).status(),404);
  const button=page.locator('.manual-image-open').filter({has:page.locator('img[src$="screen-login.png"]')});
  await button.click();await page.locator('#manualImageDialog').waitFor();
  await page.waitForFunction(()=>document.querySelector('#manualImage').naturalWidth>0);
  const original=await page.locator('#manualImage').evaluate(img=>img.getBoundingClientRect().width);
  await page.locator('#manualImageIn').click();assert((await page.locator('#manualImage').evaluate(img=>img.getBoundingClientRect().width))>original);
  await page.locator('#manualImageOut').click();await page.locator('#manualImageFit').click();
  await page.keyboard.press('Escape');assert.equal(await page.locator('#manualImageDialog').isVisible(),false);
  await page.locator('.manual-image-open').first().click();await page.locator('#manualImageClose').click();
  await page.setViewportSize({width:390,height:844});await page.reload();
  await page.waitForFunction(()=>[...document.querySelectorAll('#manual img')].length===23&&[...document.querySelectorAll('#manual img')].every(img=>img.complete&&img.naturalWidth>0));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('.manual-image-open').first().click();
  assert(await page.locator('#manualImageDialog').evaluate(el=>el.getBoundingClientRect().right<=innerWidth));
  await page.locator('#manualImageIn').click();await page.locator('#manualImageClose').click();
  await page.setViewportSize({width:1440,height:1000});await page.emulateMedia({media:'print'});
  assert.equal(await page.locator('.header-actions').isVisible(),false);
  assert.equal(await page.locator('.manual-image-hint').first().isVisible(),false);
  assert.equal(await page.locator('#manual .manual-figure').count(),23);
  if(process.env.WMT_MANUAL_PDF)await page.pdf({path:process.env.WMT_MANUAL_PDF,format:'A4',printBackground:true,margin:{top:'12mm',bottom:'12mm',left:'12mm',right:'12mm'}});
  assert.deepEqual(errors,[]);
  console.log('PASS: protected illustrated manual; 19 chapters, 16 screenshots, 7 diagrams, ordered captions, correct asset types, image zoom/fit/close, mobile layout and print/PDF');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

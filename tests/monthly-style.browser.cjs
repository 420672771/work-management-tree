// Optional browser check: install Playwright, run against a server with the fictional demo generated.
// WMT_TEST_URL=http://127.0.0.1:5100 node tests/monthly-style.browser.cjs
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_TEST_URL||'http://127.0.0.1:5100';
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1680,height:1100}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/monthly-report-demo');await page.locator('#monthlyMap svg').waitFor();
  assert.equal(await page.locator('.monthly-panel').isVisible(),false);
  assert.equal(await page.locator('[data-node="root"]>rect').getAttribute('fill'),'#00ad5b');
  const labels=await page.locator('#monthlyMap [data-node]').evaluateAll(nodes=>nodes.map(node=>{
   const rect=node.querySelector('rect'),left=Number(rect.getAttribute('x')),width=Number(rect.getAttribute('width'));
   return {text:node.getAttribute('aria-label'),rendered:Array.from(node.children).filter(n=>n.tagName==='text').map(n=>n.textContent).join(''),
    contained:Array.from(node.children).filter(n=>n.tagName==='text').every(text=>{const box=text.getBBox();return box.x>=left-1&&box.x+box.width<=left+width+1;})};
  }));
  assert(labels.length>20);for(const node of labels){assert.equal(node.rendered,node.text.replace(/\n/g,''));assert(node.contained,'Text exceeds underline: '+node.text);}
  const total=await page.locator('[data-node]').count();await page.locator('[data-map="collapse"]').click();assert((await page.locator('[data-node]').count())<total);
  await page.locator('[data-map="expand"]').click();assert.equal(await page.locator('[data-node]').count(),total);
  await page.locator('[data-map="actual"]').click();assert.equal(await page.locator('#monthlyZoom').textContent(),'100%');
  await page.locator('[data-map="width"]').click();await page.locator('[data-map="fit"]').click();
  await page.locator('[data-map="fullscreen"]').click();
  let box=await page.locator('.monthly-canvas').boundingBox();assert.equal(box.x,0);assert.equal(box.y,0);assert.equal(box.width,1680);assert.equal(box.height,1100);
  await page.locator('[data-node="root"]').click();assert(await page.locator('.monthly-panel').isVisible());assert(await page.locator('#monthlyNodeText').isDisabled());
  await page.locator('[data-map="editor"]').click();assert.equal(await page.locator('.monthly-panel').isVisible(),false);
  await page.keyboard.press('Escape');assert.equal(await page.locator('.is-fullscreen').count(),0);assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
  const downloadPromise=page.waitForEvent('download');await page.locator('[data-action="svg"]').click();const download=await downloadPromise;
  if(process.env.WMT_STYLE_SVG)await download.saveAs(process.env.WMT_STYLE_SVG);
  await page.locator('[data-map="fullscreen"]').click();await page.locator('[data-map="fit"]').click();
  if(process.env.WMT_STYLE_SCREENSHOT)await page.screenshot({path:process.env.WMT_STYLE_SCREENSHOT});
  await page.keyboard.press('Escape');await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('#monthlyMap svg').waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('[data-map="fullscreen"]').click();box=await page.locator('.monthly-canvas').boundingBox();assert.equal(box.width,390);assert.equal(box.height,844);
  await page.locator('[data-map="fit"]').click();await page.locator('[data-node="root"]').click();assert(await page.locator('.monthly-panel').isVisible());
  await page.locator('[data-map="editor"]').click();await page.locator('[data-map="fullscreen"]').click();
  assert.deepEqual(errors,[]);
  console.log('PASS: right-facing underline style, full labels inside bounds, collapse/expand, zoom, desktop/mobile fullscreen, details, SVG export');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

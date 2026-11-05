// Uses the isolated fictional fixture from scripts/demo-monthly-report.py.
// Start with WMT_DB=<fixture>/demo.db WMT_MONTHLY_DEMO=1 WMT_AUTH_MODE=demo PORT=5124.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_TEST_URL||'http://127.0.0.1:5124';
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1680,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>typeof currentUsers!=='undefined'&&currentUsers.length>0);
  const users=await page.evaluate(()=>currentUsers),a=users.find(u=>u.name==='陈晨（演示）'),b=users.find(u=>u.name==='赵悦（演示）'),empty=users.find(u=>u.name==='孙然（演示）');
  assert(a&&b&&empty,'Run only against the isolated monthly demo fixture');
  const state=await (await page.request.get(base+'/api/session')).json();assert(state.monthly_demo);assert.equal(state.authenticated,false);
  await page.locator('#demoActor').selectOption(String(a.id));await page.locator('#monthlyTab').click();await page.locator('#monthlyMonth').fill('2026-10');await page.locator('#monthlyGenerate').click();
  await page.waitForFunction(name=>document.querySelector('#monthlyTitle').textContent.includes(name),a.name);
  await page.locator('[data-node="summary:conclusion"]').click();await page.locator('#monthlyNodeText').fill('A 的编辑：完成样本补充并恢复联调');
  const aId=await page.locator('#monthlyList [data-report]').first().getAttribute('data-report');
  // Switch immediately, before the debounce fires. A must be saved using A's identity.
  await page.locator('#demoActor').selectOption(String(b.id));
  await page.waitForFunction(name=>document.querySelector('#monthlyTitle').textContent.includes(name)&&!document.querySelector('#monthlyReport').hidden,b.name);
  const aReport=await (await page.request.get(base+'/api/monthly-reports/'+aId+'?actor_id='+a.id)).json();
  assert.equal(aReport.editorial.outline.children.at(-1).children[0].text,'A 的编辑：完成样本补充并恢复联调');
  await page.locator('[data-node="summary:conclusion"]').click();assert.notEqual(await page.locator('#monthlyNodeText').inputValue(),'A 的编辑：完成样本补充并恢复联调');
  await page.locator('#monthlyNodeText').fill('B 的编辑：安排业务看板验收');await page.locator('[data-action="archive"]').click();
  await page.waitForFunction(()=>document.querySelector('#monthlyMeta').textContent.includes('已归档'));
  await page.locator('[data-action="revise"]').click();await page.waitForFunction(()=>document.querySelector('#monthlyMeta').textContent.includes('V2'));
  await page.locator('#demoActor').selectOption(String(a.id));await page.waitForFunction(name=>document.querySelector('#monthlyTitle').textContent.includes(name),a.name);
  // Failed saves block a switch and retain both the old identity and input.
  const pattern=base+'/api/monthly-reports/'+aId;
  await page.route(pattern,async route=>{if(route.request().method()==='POST')await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'模拟保存失败'})});else await route.continue();});
  await page.locator('[data-node="summary:conclusion"]').click();await page.locator('#monthlyNodeText').fill('A 未保存的输入仍保留');
  await page.locator('#demoActor').selectOption(String(b.id));await page.locator('#monthlyRetry').waitFor();
  assert.equal(await page.locator('#demoActor').inputValue(),String(a.id));assert.equal(await page.locator('#monthlyNodeText').inputValue(),'A 未保存的输入仍保留');
  await page.unroute(pattern);await page.locator('#monthlyRetry').click();await page.waitForFunction(()=>document.querySelector('#monthlySaveState').textContent==='已自动保存');
  // Slow old responses must not restore the previous employee's list or report.
  await page.locator('#demoActor').selectOption(String(b.id));await page.waitForFunction(name=>document.querySelector('#monthlyTitle').textContent.includes(name),b.name);
  let release,started;const held=new Promise(r=>release=r),arrived=new Promise(r=>started=r);
  const delayed='**/api/monthly-reports?**';await page.route(delayed,async route=>{
   const url=new URL(route.request().url());if(url.searchParams.get('actor_id')===String(a.id)){started();await held;}await route.continue();
  });
  await page.locator('#demoActor').selectOption(String(a.id));await arrived;
  await page.locator('#demoActor').selectOption(String(b.id));await page.waitForFunction(name=>document.querySelector('#monthlyTitle').textContent.includes(name)&&!document.querySelector('#monthlyReport').hidden,b.name);release();
  await page.unroute(delayed,{behavior:'wait'});
  assert((await page.locator('#monthlyTitle').textContent()).includes(b.name));
  await page.locator('#demoActor').selectOption(String(empty.id));await page.waitForFunction(name=>document.querySelector('#monthlyAuth').textContent.includes(name),empty.name);
  await page.locator('#monthlyMonth').fill('2026-09');await page.locator('#monthlyGenerate').click();await page.locator('#monthlyMap svg').waitFor();
  assert((await page.locator('#monthlyTitle').textContent()).includes(empty.name));
  const downloadPromise=page.waitForEvent('download');await page.locator('[data-action="json"]').click();await downloadPromise;
  await page.locator('#homeTab').click();await page.locator('#monthlyPage').waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
  console.log('PASS: main-page demo identities, generate/edit/archive/revise/export, save before switching, failed-save switch rollback, stale-response protection');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

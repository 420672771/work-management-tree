// Use the disposable account-mode database; never point this at a live server.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {base,task,confirmations}=JSON.parse(require('node:fs').readFileSync(process.env.WMT_PENDING_DRAFT_FIXTURE));
assert(base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/login');await page.locator('#loginName').fill('draft.pm');
  await page.locator('#loginPassword').fill('DraftFixture2026!');await page.locator('#loginSubmit').click();await page.waitForURL(base+'/');
  await page.locator('#tasksTab').click();await page.getByRole('button',{name:'待确认任务责任域回归测试',exact:true}).click();
  const history=page.locator('.task-progress-history'),items=history.locator('.progress-history-item');
  await items.nth(9).waitFor();assert.equal(await items.count(),10);
  assert.match(await history.textContent(),/（14）/);
  assert.match(await items.nth(0).textContent(),/65%.*待确认/s);
  assert.doesNotMatch(await items.nth(0).textContent(),/飞书|网页/);
  assert.match(await items.nth(0).textContent(),/等待业务确认/);
  assert.match(await items.nth(0).textContent(),/2030\/11\/01 10:00/);
  assert.match(await items.nth(1).textContent(),/已驳回.*请补充验收证据/s);
  assert.match(await items.nth(2).textContent(),/自动生效/);
  assert.match(await items.nth(3).textContent(),/已确认/);
  assert.doesNotMatch(await items.nth(3).textContent(),/确认人|处理说明/);
  assert.equal(await history.getByRole('progressbar').count(),10);
  const bar=items.first().getByRole('progressbar');assert.equal(await bar.getAttribute('aria-valuenow'),'65');
  assert(await bar.evaluate(el=>el.offsetHeight<=5&&Math.abs(el.firstElementChild.getBoundingClientRect().width/el.getBoundingClientRect().width-.65)<.02));
  const desktopBar=await bar.boundingBox(),desktopTime=await items.first().locator('time').boundingBox();
  assert(Math.abs(desktopBar.y+desktopBar.height/2-desktopTime.y-desktopTime.height/2)<4);
  assert.equal(await items.nth(3).getByRole('button',{name:'展开',exact:true}).count(),0);
  assert.equal(await history.locator('img').count(),0); // Untrusted report text stays text.
  await history.getByRole('button',{name:/加载更早反馈/}).click();assert.equal(await items.count(),14);
  assert.match(await items.last().textContent(),/0%/);
  assert.equal(await items.last().getByRole('progressbar').getAttribute('aria-valuenow'),'0');
  assert(await items.last().getByRole('progressbar').evaluate(el=>el.firstElementChild.getBoundingClientRect().width===0));
  const expand=items.last().getByRole('button',{name:'展开',exact:true}),longText=items.last().locator('.progress-history-text');
  await expand.waitFor();
  assert(await longText.evaluate(el=>el.clientHeight<el.scrollHeight));
  await expand.click();assert(await longText.evaluate(el=>el.clientHeight>=el.scrollHeight-1));
  await items.last().getByRole('button',{name:'收起',exact:true}).click();
  assert(await longText.evaluate(el=>el.clientHeight<el.scrollHeight));
  await page.route('**/api/progress?*',route=>route.abort());
  await history.getByRole('button',{name:'刷新记录'}).click();await history.getByRole('alert').waitFor();
  assert.equal(await history.getByRole('button',{name:'刷新记录'}).isEnabled(),true);
  await page.unroute('**/api/progress?*');await history.getByRole('button',{name:'刷新记录'}).click();await items.nth(9).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert(await history.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  assert(await items.first().evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  const mobileBar=await bar.boundingBox(),mobileStatus=await items.first().locator('.progress-history-status').boundingBox();
  assert(Math.abs(mobileBar.y+mobileBar.height/2-mobileStatus.y-mobileStatus.height/2)<4);
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole('button',{name:'查看历史反馈',exact:true}).click();
  assert(await bar.evaluate(el=>{const r=el.getBoundingClientRect();return !!document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.progress-history-mini-track');}),'Inline bar must remain visible after jumping to history');
  await page.screenshot({path:'/tmp/wmt-progress-history.png'});
  // Opening an empty task cannot retain the previous task's reports.
  await page.evaluate(id=>showTaskDetails(id),confirmations.pm_self);
  await history.getByText('暂无进度反馈记录。').waitFor();assert.equal(await items.count(),0);
  // Similar-task detail uses a separate asynchronous entry point.
  await page.evaluate(id=>openSimilarTaskDetail(id),task);await items.nth(9).waitFor();
  // A late history response must not append records to an edit form.
  let release;await page.route('**/api/progress?*',async route=>{await new Promise(resolve=>release=resolve);await route.continue();});
  await history.getByRole('button',{name:'刷新记录'}).click();await page.waitForFunction(()=>document.querySelector('.progress-history-refresh')?.disabled);
  await page.evaluate(id=>editTask(id),task);await page.locator('#taskEditForm').waitFor();
  const received=page.waitForResponse(response=>response.url().includes('/api/progress?'));
  release();await (await received).finished();await page.unroute('**/api/progress?*',{behavior:'wait'});
  assert.equal(await page.locator('#taskEditForm .task-progress-history').count(),0);
  assert.deepEqual(errors,[]);await context.close();
  console.log('PASS: compact history with small inline progress bars, correct 65% and 0% fill, desktop/mobile alignment, key metadata and exceptions, safe text, expand/collapse, load older, retry and detail/edit entry points');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

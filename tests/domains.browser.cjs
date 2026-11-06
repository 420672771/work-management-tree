// Run only against the explicit isolated fixture supplied by WMT_DOMAIN_FIXTURE.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const config=JSON.parse(require('node:fs').readFileSync(process.env.WMT_DOMAIN_FIXTURE));
assert(config.base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(config.base);await page.waitForFunction(()=>typeof currentUsers!=='undefined'&&currentUsers.length>0);
  await page.evaluate(id=>setDemoActor(id),config.manager);
  await page.locator('#orgTab').click();await page.locator('#orgPosition').waitFor({state:'visible'});
  await page.locator('#orgDomainsTab').click();await page.locator('#domainList table').waitFor();
  await page.locator('#domainCreate').click();await page.locator('#domainName').fill('浏览器自定义责任域');
  await page.locator('#domainCode').fill('BROWSER');await page.locator('#domainOwner').selectOption(String(config.member));
  await page.locator('#domainBoundary').fill('自定义的工作责任范围');await page.locator('#domainSave').click();
  await page.locator('#domainDialog').waitFor({state:'hidden'});
  await page.locator('#domainMessage').filter({hasText:'责任域已新增'}).waitFor();
  const rows=await (await page.request.get(config.base+'/api/domains?actor_id='+config.manager)).json();
  const created=rows.items.find(row=>row.name==='浏览器自定义责任域');assert(created);assert(created.can_delete);
  assert.equal(await page.locator('#taskNode option[value="'+created.id+'"]').count(),1);
  assert.equal(await page.locator('#routineNode option[value="'+created.id+'"]').count(),1);
  const row=id=>page.locator('#domainList tr').filter({has:page.locator('[data-domain-edit="'+id+'"]')});
  await row(created.id).getByRole('button',{name:'修改',exact:true}).click();
  await page.locator('#domainName').fill('浏览器历史责任域');await page.locator('#domainSave').click();
  await page.locator('#domainFormMessage').filter({hasText:'名称已存在'}).waitFor();assert(await page.locator('#domainDialog').isVisible());
  await page.locator('#domainName').fill('浏览器修改责任域');await page.locator('#domainSave').click();
  await page.locator('#domainDialog').waitFor({state:'hidden'});await row(created.id).getByText('浏览器修改责任域',{exact:true}).waitFor();
  await row(created.id).getByRole('button',{name:'删除',exact:true}).click();await page.locator('#actionConfirmSubmit').click();
  await row(created.id).waitFor({state:'detached'});
  assert(await row(config.domain).getByRole('button',{name:'删除',exact:true}).isDisabled());
  const rejected=await page.evaluate(async cfg=>{
   const response=await fetch('/api/domains/'+cfg.domain,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'delete',actor_id:cfg.manager})});
   return {status:response.status,body:await response.json()};
  },config);assert.equal(rejected.status,409);assert(rejected.body.error.includes('已被引用'));
  const report=async(type,id)=>{const response=await page.request.get(config.base+'/api/'+type+'-reports/'+id+'?actor_id='+config.manager);assert(response.ok());return response.json();};
  const previousWeek=await report('weekly',config.weekly),previousMonth=await report('monthly',config.monthly);
  await row(config.domain).getByRole('button',{name:'修改',exact:true}).click();
  await page.locator('#domainName').fill('历史责任域已更新');await page.locator('#domainBoundary').fill('引用后仍可修改的责任范围');
  await page.locator('#domainSave').click();await page.locator('#domainDialog').waitFor({state:'hidden'});
  await page.locator('#domainMessage').filter({hasText:'历史数据已同步更新'}).waitFor();
  const week=await report('weekly',config.weekly),month=await report('monthly',config.monthly);
  for(const [before,after] of [[previousWeek,week],[previousMonth,month]]){
   assert.deepEqual(after.snapshot.metrics,before.snapshot.metrics);assert.equal(after.status,before.status);
   assert(after.snapshot.tasks.some(task=>task.domain_name==='历史责任域已更新'));
  }
  const text=await (await page.request.get(config.base+'/api/monthly-reports/'+config.monthly+'/text?actor_id='+config.manager)).json();
  assert(text.text.includes('历史责任域已更新'));
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.evaluate(id=>setDemoActor(id),config.member);await page.locator('#orgDomainsTab').waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
  console.log('PASS: domain menu/create/edit/delete, duplicate validation, selector updates, referenced deletion guard, published/archived history synchronization, role visibility, mobile layout');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

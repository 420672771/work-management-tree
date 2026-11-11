// Disposable account-mode fixture: python tests/check_pending_drafts.py tests/task-confirmations.browser.cjs
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {base,users,confirmations}=JSON.parse(require('node:fs').readFileSync(process.env.WMT_PENDING_DRAFT_FIXTURE));
assert(base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  for(const role of ['member','pm','manager']){
   const context=await browser.newContext(),page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(base+'/login');await page.locator('#loginName').fill('draft.'+role);
   await page.locator('#loginPassword').fill('DraftFixture2026!');await page.locator('#loginSubmit').click();
   await page.waitForURL(base+'/');await page.locator('#approvalsTab').click();
   const card=title=>page.locator('#approvals .approval-card').filter({has:page.getByRole('button',{name:title,exact:true})});
   if(role==='member'){
    await card('待确认任务责任域回归测试').waitFor();
    assert.equal(await card('PM上级确认测试').count(),0);
    assert.equal(await card('负责人本人确认测试').count(),0);
    assert.equal((await page.request.get(base+'/api/tasks/'+confirmations.pm_superior)).status(),404);
    assert.deepEqual(errors,[]);await context.close();continue;
   }
   await card('PM上级确认测试').waitFor();
   const approve=async(title,key)=>{
    await card(title).getByRole('button',{name:'确认',exact:true}).click();
    await page.locator('#actionConfirmSubmit').click();await card(title).waitFor({state:'detached'});
    const task=await (await page.request.get(base+'/api/tasks/'+confirmations[key])).json();
    assert.equal(task.approval_status,'approved');assert.equal(task.confirmed_by,users[role]);
   };
   if(role==='member'){
    assert.equal(await card('PM上级确认测试').getByRole('button',{name:'确认',exact:true}).count(),0);
    assert.equal(await card('负责人本人确认测试').getByRole('button',{name:'确认',exact:true}).count(),0);
   }else{
    assert(await card('PM上级确认测试').getByRole('button',{name:'确认',exact:true}).isVisible());
    await card('PM上级确认测试').getByRole('button',{name:'查看详情',exact:true}).click();
    await page.locator('#approvalDetailDialog').getByRole('button',{name:'确认',exact:true}).waitFor();
    assert(await page.locator('#approvalDetailContent').getByText('可确认人',{exact:true}).isVisible());
    await page.locator('#approvalDetailDialog .approval-close-icon').click();
    if(role==='pm'){
     assert.equal(await card('负责人本人确认测试').getByRole('button',{name:'确认',exact:true}).count(),0);
     await approve('PM本人确认测试','pm_self');
    }else{
     await approve('PM上级确认测试','pm_superior');
     await approve('负责人本人确认测试','manager_self');
    }
    await page.locator('#homeTab').click();
    assert.equal(await page.locator('#owners option[value="'+users.manager+'"]').count(),1);
   }
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: account-mode PM self/superior and manager self confirmation, unauthorized buttons hidden, list/detail consistent, actual operator recorded, manager owner selection available');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

// Run with: WMT_PLAYWRIGHT_MODULE=/path/to/playwright python tests/check_pending_drafts.py
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fixture=JSON.parse(require('node:fs').readFileSync(process.env.WMT_PENDING_DRAFT_FIXTURE));
const {base,task,approval,domains,users}=fixture;
assert(base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  for(const role of ['manager','pm','member','other']){
   const context=await browser.newContext(),page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(base+'/login');await page.locator('#loginName').fill('draft.'+role);
   await page.locator('#loginPassword').fill('DraftFixture2026!');await page.locator('#loginSubmit').click();
   await page.waitForURL(base+'/');await page.evaluate(()=>openPage('approvals'));
   if(role==='other'){
    await page.waitForFunction(()=>typeof currentUsers!=='undefined'&&currentUsers.length>0);
    const response=await page.request.post(base+'/api/tasks/'+task,{data:{action:'update',operator_id:users.manager,org_node_id:domains[0]}});
    assert.equal(response.status(),404);
    assert.equal(await page.locator('#approvals .pending-draft-actions').count(),0);
    await context.close();continue;
   }
   const card=page.locator('#approvals .approval-card').filter({hasText:'待确认任务责任域回归测试'});
   const open=async()=>{await card.getByRole('button',{name:'修改草稿',exact:true}).click();await page.locator('#pendingDraftDialog').waitFor();};
   const read=async()=>{const response=await page.request.get(base+'/api/tasks/'+task);assert(response.ok());return response.json();};
   const before=await read();
   await open();
   // The real page's multi-draft simplifier removes legacy deliverable controls.
   await page.waitForFunction(()=>!document.querySelector('#pendingDraftForm [name="deliverable"]'));
   const target=domains.find(id=>id!==before.org_node_id);
   await page.locator('#pendingDraftForm [name="org_node_id"]').selectOption(String(target));
   const submit=page.locator('#pendingDraftForm [type="submit"]');
   await submit.click();
   try{await page.locator('#pendingDraftDialog').waitFor({state:'hidden',timeout:4000});}
   catch(error){console.error({role,pageErrors:errors,saveLabel:await submit.textContent()});throw error;}
   const after=await read();assert.equal(after.org_node_id,target);
   for(const key of ['title','description','deliverable','owner_id','due_at','priority','approver_id','approval_status','work_status'])assert.deepEqual(after[key],before[key],key);
   const pending=await (await page.request.get(base+'/api/approvals/'+approval)).json();
   assert.equal(pending.status,'pending');assert.equal(pending.approver_id,before.approver_id);
   if(role==='manager'){
    await open();
    // Rejected requests must keep the form, show a visible error and allow retry.
    await page.locator('#pendingDraftForm [name="org_node_id"]').selectOption(String(before.org_node_id));
    await page.route('**/api/tasks/'+task,route=>route.request().method()==='POST'?route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'责任域不可用，请重新选择'})}):route.continue());
    await submit.click();await page.locator('#pendingDraftForm [role="alert"]').filter({hasText:'责任域不可用'}).waitFor();
    assert(await submit.isEnabled());assert.equal((await read()).org_node_id,target);
    await page.unroute('**/api/tasks/'+task);
    await page.route('**/api/tasks/'+task,route=>route.request().method()==='POST'?route.abort('failed'):route.continue());
    await submit.click();await page.locator('#pendingDraftForm [role="alert"]').filter({hasText:/Failed to fetch|失败/}).waitFor();assert(await submit.isEnabled());
    await page.unroute('**/api/tasks/'+task);
    await submit.click();await page.locator('#pendingDraftDialog').waitFor({state:'hidden'});
    assert.equal((await read()).org_node_id,before.org_node_id);
   }
   if(role==='member'){
    await open();
    await page.locator('#pendingDraftForm [name="owner_id"]').selectOption(String(users.pm));
    await page.locator('#pendingDraftForm [name="due_at"]').fill('2030-12-01');
    await page.locator('#pendingDraftForm [name="priority"]').selectOption('P0');
    await page.locator('#pendingDraftForm [name="description"]').fill('已补充任务范围与业务约束');
    await submit.click();await page.locator('#pendingDraftDialog').waitFor({state:'hidden'});
    assert.equal((await page.request.get(base+'/api/tasks/'+task)).status(),404);
    const nextOwner=await browser.newContext();
    assert((await nextOwner.request.post(base+'/api/auth/login',{data:{username:'draft.pm',password:'DraftFixture2026!'}})).ok());
    const changed=await (await nextOwner.request.get(base+'/api/tasks/'+task)).json();assert.equal(changed.owner_id,users.pm);assert.equal(changed.due_at,'2030-12-01T17:00:00+08:00');
    assert.equal(changed.priority,'P0');assert.equal(changed.description,'已补充任务范围与业务约束');assert.equal(changed.deliverable,before.deliverable);
    const chain=await (await nextOwner.request.get(base+'/api/approvals/'+approval)).json();
    assert.equal(chain.status,'pending');assert.equal(chain.approver_id,changed.approver_id);assert.equal(chain.approver_id,users.manager);
    await nextOwner.close();
   }
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: full-page pending domain edits as manager/PM/owner, legacy control removal, original data preserved, owner/date/priority/details edits and confirmation chain, visible API/network errors and retry, unrelated member denied');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});

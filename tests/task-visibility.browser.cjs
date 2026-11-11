// Isolated cookie-authenticated fixture; exercise complete pages and direct API calls.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {base,users,confirmations,visibility}=JSON.parse(require('node:fs').readFileSync(process.env.WMT_PENDING_DRAFT_FIXTURE));
assert(base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  for(const role of ['member','leader','other','pm','manager']){
   const context=await browser.newContext(),page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   // Persisted demo identity must never override a real login.
   await context.addInitScript(id=>localStorage.setItem('wmt_demo_actor_id',String(id)),users.manager);
   await page.goto(base+'/login');await page.locator('#loginName').fill('draft.'+role);
   await page.locator('#loginPassword').fill('DraftFixture2026!');await page.locator('#loginSubmit').click();
   await page.waitForURL(base+'/');await page.locator('#approvalsTab').click();
   const privateCard=page.locator('#approvals .approval-card').filter({hasText:'PM上级确认测试'});
   const management=['pm','manager'].includes(role);
   await page.waitForFunction(()=>{const text=document.querySelector('#approvals')?.textContent;return text&&!text.includes('加载中');});
   if(management){await privateCard.waitFor();}
   else{
    assert.equal(await privateCard.count(),0);
    assert.equal(await page.locator('#approvals .approval-card').filter({hasText:'负责人本人确认测试'}).count(),0);
    assert.equal((await page.request.get(base+'/api/tasks/'+confirmations.pm_superior)).status(),404);
    assert.equal((await page.request.get(base+'/api/tasks/'+visibility.private)).status(),404);
   }
   const bootstrap=await (await page.request.get(base+'/api/bootstrap?actor_id='+users.manager)).json();
   const ids=bootstrap.tasks.map(task=>task.id);
   assert.equal(ids.includes(visibility.private),management);
   assert.equal(ids.includes(visibility.own),management||['member','leader'].includes(role));
   assert.equal(bootstrap.dashboard.total,management?2:role==='other'?0:1);
   const drafts=await (await page.request.get(base+'/api/task/drafts?actor_id='+users.manager)).json();
   assert.equal(drafts.some(draft=>draft.slots.title==='管理PM正式任务示例草稿'),management);
   const ownDraft=drafts.find(draft=>draft.slots.title==='成员正式任务示例草稿');
   assert.equal(!!ownDraft,management||['member','leader'].includes(role));
   if(ownDraft){
    assert.equal(ownDraft.can_edit,role!=='leader');
    await page.evaluate(()=>openApprovalSection('approvalDrafts'));
    const card=page.locator('#conversationDrafts [data-draft-id="'+ownDraft.id+'"]');await card.waitFor();
    if(role==='leader'){
     assert.equal(await card.locator('button').count(),0);assert(await card.getByText('只读查看',{exact:false}).isVisible());
     const response=await page.request.post(base+'/api/task/draft/'+ownDraft.id+'/discard',{data:{}});assert.equal(response.status(),404);
    }else{assert(await card.getByRole('button',{name:'确认生成',exact:true}).isVisible());}
   }
   await page.locator('#tasksTab').click();
   assert.equal(await page.locator('#tasks .task').filter({hasText:'管理PM正式任务示例'}).count(),management?1:0);
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: member/team/management data visibility in real pages and APIs, private PM/head approvals absent, scoped totals, task details denied, signed identity defeats demo/parameter spoofing, team drafts read-only');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

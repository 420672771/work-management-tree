// Run only against the disposable account-mode fixture, with real session cookies.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {base,users,self_confirmation:tasks}=JSON.parse(require('node:fs').readFileSync(process.env.WMT_PENDING_DRAFT_FIXTURE));
assert(base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  for(const role of ['pm','manager']){
   const context=await browser.newContext(),page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
   await page.goto(base+'/login');await page.locator('#loginName').fill('draft.'+role);
   await page.locator('#loginPassword').fill('DraftFixture2026!');await page.locator('#loginSubmit').click();await page.waitForURL(base+'/');
   await page.locator('#tasksTab').click();
   const progressTitle=role==='pm'?'PM进度本人确认示例':'负责人进度本人确认示例';
   const taskCard=title=>page.locator('#tasks .task').filter({has:page.getByRole('button',{name:title,exact:true})});
   await taskCard(progressTitle).getByRole('button',{name:'反馈',exact:true}).click();
   await page.locator('#progressFeedbackPercent').fill('45');await page.locator('#progressFeedbackComment').fill('已完成方案初稿与主要场景验证');
   await page.locator('#progressFeedbackSubmit').click();await page.locator('#progressFeedbackDialog').waitFor({state:'hidden'});
   const read=async id=>{const response=await page.request.get(base+'/api/tasks/'+id);assert(response.ok());return response.json();};
   assert.equal((await read(tasks[role+'_progress'])).effective_percent,0);
   await page.locator('#approvalsTab').click();
   const approval=page.locator('#approvals .approval-card').filter({has:page.getByRole('button',{name:progressTitle,exact:true})});
   await approval.getByRole('button',{name:'确认',exact:true}).click();
   await page.locator('#actionConfirmDialog').waitFor({state:'visible'});
   assert.equal(await page.locator('#actionConfirmTitle').textContent(),'确认进度');
   await page.locator('#actionConfirmSubmit').click();await approval.waitFor({state:'detached'});
   const confirmed=await read(tasks[role+'_progress']);assert.equal(confirmed.effective_percent,45);assert.equal(confirmed.latest_progress.confirmed_by,users[role]);
   await page.locator('#tasksTab').click();
   const accept=async(title,key)=>{
    await taskCard(title).getByRole('button',{name:'验收通过',exact:true}).click();
    await page.waitForFunction(id=>currentTasks.find(task=>task.id===id)?.work_status==='completed',tasks[key]);
    const completed=await read(tasks[key]);assert.equal(completed.latest_acceptance.acceptor_id,users[role]);assert.equal(completed.work_status,'completed');
   };
   if(role==='pm'){
    assert.equal(await taskCard('负责人交付本人验收示例').getByRole('button',{name:'验收通过',exact:true}).count(),0);
    assert.equal(await taskCard('普通成员交付验收示例').getByRole('button',{name:'验收通过',exact:true}).count(),0);
    await accept('PM交付本人验收示例','pm_accept');
   }else{
    await accept('负责人交付本人验收示例','manager_accept');await accept('PM交付上级验收示例','pm_superior_accept');
   }
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: PM/head feedback waits for manual self confirmation, correct progress dialog, effective progress and real actor, PM/head self acceptance, superior PM acceptance, unrelated acceptance buttons hidden');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

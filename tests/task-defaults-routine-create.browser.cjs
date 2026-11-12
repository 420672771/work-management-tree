const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {base,users,domains}=JSON.parse(require('node:fs').readFileSync(process.env.WMT_PENDING_DRAFT_FIXTURE));
assert(base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
  for(const role of ['pm','member','manager']){
   const context=await browser.newContext(),page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(base+'/login');await page.locator('#loginName').fill('draft.'+role);
   await page.locator('#loginPassword').fill('DraftFixture2026!');await page.locator('#loginSubmit').click();await page.waitForURL(base+'/');
   await page.waitForFunction(()=>document.querySelector('#owners')?.options.length>0);
   assert.equal(await page.locator('#owners').inputValue(),String(users[role]));
   const other=role==='pm'?users.member:users.pm;
   await page.locator('#owners').selectOption(String(other));await page.evaluate(()=>load());
   assert.equal(await page.locator('#owners').inputValue(),String(other)); // Refresh preserves an explicit choice.
   await page.evaluate(()=>document.querySelector('#form').reset());
   assert.equal(await page.locator('#owners').inputValue(),String(users[role]));
   if(role==='pm'){
    // Actual submission, not just form.reset(), must restore the current user.
    await page.locator('#owners').selectOption(String(users.member));
    await page.locator('#taskTitle').fill('新建责任人默认值验证任务');await page.locator('#taskDueAt').fill('2030-11-20');
    await page.locator('#form .task-advanced-fields').evaluate(el=>el.open=true);
    await page.locator('#form [name="estimated_hours"]').fill('4');
    const created=page.waitForResponse(response=>response.url().endsWith('/api/tasks')&&response.request().method()==='POST');
    await page.locator('#form .form-actions button').click();const response=await created;assert(response.ok());
    const task=await response.json();const saved=await (await page.request.get(base+'/api/tasks/'+task.id)).json();
    assert.equal(saved.owner_id,users.member);await page.waitForFunction(()=>document.querySelector('#form [name="title"]').value==='');
    assert.equal(await page.locator('#owners').inputValue(),String(users.pm));
    await page.evaluate(()=>openPage('org'));await page.evaluate(()=>openOrgSection('orgRoutine'));
    await page.locator('#orgRoutine>.org-panel-action button').click();await page.locator('#routineDialog').waitFor();
    await page.locator('#routineTitle').fill('保存即生成本周任务示例');await page.locator('#routineNode').selectOption(String(domains[0]));
    for(const uid of [users.pm,users.member])await page.locator('#routineOwnerOptions input[value="'+uid+'"]').check();
    await page.locator('#routineDeliverable').fill('每周维护记录');await page.locator('#routineHours').fill('2');
    const savedTemplate=page.waitForResponse(response=>response.url().endsWith('/api/routine/templates')&&response.request().method()==='POST');
    await page.locator('#routineForm button[type="submit"]').click();const templateResponse=await savedTemplate;assert(templateResponse.ok());
    const template=await templateResponse.json();assert.equal(template.count,2);
    await page.locator('#routineDialog').waitFor({state:'hidden'});await page.waitForFunction(()=>document.querySelector('#routineSaveMessage')?.textContent.includes('已生成 2 条本周例行任务'));
    const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    for(const id of template.generated_task_ids){const task=await (await page.request.get(base+'/api/tasks/'+id)).json();assert(task.routine_week_start<=today&&today<=task.routine_week_end);assert.equal(task.approval_status,'approved');}
    const repeated=await page.request.post(base+'/api/routine/generate',{data:{instance_date:today,template_ids:[template.id]}});assert.equal((await repeated.json()).count,0);
    await page.evaluate(()=>openPage('tasks'));await page.getByRole('button',{name:'保存即生成本周任务示例',exact:true}).first().waitFor();
    // Exercise the existing demo identity switch without changing the signed server identity.
    await page.evaluate(async id=>{openPage('home');sessionUser=null;renderDemoActor();await setDemoActor(id);},users.member);
    assert.equal(await page.locator('#owners').inputValue(),String(users.member));
    await page.locator('#owners').selectOption(String(users.pm));
    await page.evaluate(id=>setDemoActor(id),users.manager);
    assert.equal(await page.locator('#owners').inputValue(),String(users.manager));
   }
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: role-specific default owner, preserve manual choice, reset after submit, template save immediately creates independent current-week tasks, repeated run skips duplicates');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

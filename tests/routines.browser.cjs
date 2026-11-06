// Requires an explicitly isolated demo server and fixture IDs; never defaults to the live app.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const config=JSON.parse(require('node:fs').readFileSync(process.env.WMT_ROUTINE_FIXTURE));
assert(config.base.startsWith('http://127.0.0.1:'));
(async()=>{
  const browser=await chromium.launch();
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('dialog',async d=>{errors.push(d.message());await d.accept()});
    await page.goto(config.base);await page.waitForFunction(()=>currentUsers.length>0);
    await page.evaluate(id=>setDemoActor(id),config.manager);
    await page.locator('#orgTab').click();await page.locator('#orgPosition').waitFor({state:'visible'});
    await page.locator('#orgRoutineTab').click();await page.locator('#orgRoutine').waitFor({state:'visible'});
    await page.waitForFunction(()=>Array.isArray(window.routineTemplates));
    const initial=await page.evaluate(()=>window.routineTemplates.length);
    await page.locator('#orgRoutine>.org-panel-action button').click();
    await page.locator('#routineHours').fill('16.5');
    assert.equal(await page.locator('#routineHours').getAttribute('required'),'');
    await page.locator('#routineTitle').fill('核心系统日常运维');
    await page.locator('#routineOwner').selectOption(String(config.owner));
    await page.locator('#routineNode').selectOption(String(config.domain));
    await page.locator('#routineDeliverable').fill('巡检、备份与异常处理记录');
    await page.screenshot({path:'/tmp/wmt-routines-template.png'});
    await page.locator('#routineForm button[type="submit"]').click();
    await page.locator('#routineDialog').waitFor({state:'hidden'});
    await page.waitForFunction(n=>window.routineTemplates?.length===n+1,initial);
    const template=await page.evaluate(()=>window.routineTemplates[0]);
    assert.equal(template.estimated_hours,16.5);assert.equal(template.recurrence,'WEEKLY');
    const generated=await page.evaluate(async()=>{
      const r=await fetch('/api/routine/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instance_date:'2026-11-09'})});return r.json();
    });
    assert(generated.count>=1);await page.evaluate(()=>load());const tid=await page.evaluate(id=>currentTasks.find(t=>t.parent_template_id===id&&t.routine_week_start==='2026-11-09').id,template.id);
    await page.evaluate(id=>editRoutineTemplate(id),template.id);
    await page.locator('#routineHours').fill('20');await page.locator('#routineForm button[type="submit"]').click();
    await page.locator('#routineDialog').waitFor({state:'hidden'});
    await page.evaluate(async id=>{await setDemoActor(id);await load();await openPage('tasks')},config.owner);
    let task=await page.evaluate(id=>currentTasks.find(t=>t.id===id),tid);
    assert.equal(task.estimated_hours,16.5);assert.equal(task.approval_status,'approved');
    assert.equal(task.routine_week_start,'2026-11-09');assert.equal(task.routine_week_end,'2026-11-15');
    for(const actual of ['18.5','20','0']){
      await page.locator(`#taskPage button[onclick="reportRoutineHours(${tid})"]`).click();
      await page.locator('#actionInputValue').fill(actual);
      await page.locator('#actionInputForm button[type="submit"]').click();
      await page.waitForFunction(({id,actual})=>currentTasks.find(t=>t.id===id)?.actual_hours===Number(actual),{id:tid,actual});
    }
    task=await page.evaluate(id=>currentTasks.find(t=>t.id===id),tid);
    assert(task.routine_hours_updated_at);assert.equal(task.work_status,'assigned');assert.equal(task.estimated_hours,16.5);
    await page.screenshot({path:'/tmp/wmt-routines-task.png'});
    await page.evaluate(id=>editTask(id),tid);
    await page.locator('#taskEditForm [name="actual_hours"]').waitFor({state:'attached'});
    assert.equal(await page.locator('#taskEditForm [name="estimated_hours"]').inputValue(),'16.5');
    assert.equal(await page.locator('#taskEditForm [name="actual_hours"]').inputValue(),'0');
    const result=await page.evaluate(async()=>{
      const request=async()=>{const r=await fetch('/api/routine/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instance_date:'2026-11-16'})});return r.json()};
      const first=await request(),second=await request();await load();return {first,second};
    });
    assert(result.first.count>=1);assert.equal(result.second.count,0);
    const next=await page.evaluate(id=>currentTasks.find(t=>t.parent_template_id===id&&t.routine_week_start==='2026-11-16'),template.id);
    assert.equal(next.estimated_hours,20);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>closeTaskDetails());
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[]);
    console.log('PASS: template create/edit, estimate snapshots, immediate activation, decimal/zero weekly hours, idempotent generation, edit fields, mobile layout.');
  }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});

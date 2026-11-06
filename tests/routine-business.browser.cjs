// Uses only the explicitly supplied retained fixture and its newly created accounts.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.WMT_ROUTINE_TEST_URL,manifestPath=process.env.WMT_ROUTINE_MANIFEST;
assert(base&&manifestPath,'Specify a test URL and the retained routine fixture manifest.');
const fixture=JSON.parse(fs.readFileSync(manifestPath));
const output=path.dirname(manifestPath);
(async()=>{
  const browser=await chromium.launch(),pages=[],errors=[],checks=[];
  const check=(name,condition)=>{assert(condition,name);checks.push({name,passed:true,detail:'真实浏览器与账号登录验证'});};
  const login=async key=>{
    const p=await browser.newPage({viewport:{width:1440,height:1050}});pages.push(p);
    p.on('pageerror',e=>errors.push(e.message));
    await p.goto(base+'/login');await p.locator('#loginName').fill(fixture.accounts[key]);await p.locator('#loginPassword').fill('123456');
    await p.locator('#loginSubmit').click();await p.waitForURL(base+'/');
    await p.waitForFunction(()=>typeof sessionUser!=='undefined'&&sessionUser&&currentUsers.length);
    check('浏览器登录身份：'+key,await p.evaluate(()=>currentActorId())===fixture.users[key]);
    return p;
  };
  try{
    const employee=await login('ops'),tid=fixture.tasks['2026-11-02'].ops;
    await employee.locator('#tasksTab').click();
    await employee.waitForFunction(id=>document.querySelector('#taskList')?.innerText.includes('[例行演练]')||currentTasks.some(t=>t.id===id),tid);
    await employee.evaluate(id=>showTaskDetails(id),tid);
    await employee.locator('#taskDetailDialog').waitFor({state:'visible'});
    check('任务详情显示生成周期和周工时',await employee.locator('#taskDetailContent').innerText().then(t=>t.includes('2026-11-02')&&t.includes('16.5h')&&t.includes('18.5h')));
    await employee.locator(`#taskDetailContent button[onclick="reportRoutineHours(${tid})"]`).click();
    check('周工时入口回显累计值',await employee.locator('#actionInputValue').inputValue()==='18.5');
    await employee.locator('#actionInputForm button[type="button"]').last().click();
    await employee.evaluate(id=>editTask(id),tid);
    await employee.locator('#taskEditForm [name="estimated_hours"]').waitFor({state:'attached'});
    check('生成任务标准工时只读',!(await employee.locator('#taskEditForm [name="estimated_hours"]').isEditable()));
    await employee.evaluate(()=>closeTaskDetails());
    await employee.locator('#performanceTab').click();
    await employee.waitForFunction(()=>document.querySelector('#performanceContent .performance-metric'));
    const month=fixture.performance.ops.month;
    check('个人绩效页面得分与接口一致',(await employee.locator('#performanceContent .performance-metric b').first().innerText()).includes(String(month.score)));
    check('绩效明细区分周周期与实际工时',await employee.locator('#performanceContent').innerText().then(t=>t.includes('2026-11-02')&&t.includes('18.5h')&&t.includes('待填')));
    await employee.screenshot({path:path.join(output,'employee-performance.png'),fullPage:true});
    const manager=await login('manager');
    await manager.locator('#orgTab').click();await manager.locator('#orgPosition').waitFor({state:'visible'});
    await manager.locator('#orgRoutineTab').click();await manager.locator('#orgRoutine').waitFor({state:'visible'});
    await manager.waitForFunction(()=>Array.isArray(window.routineTemplates));
    check('负责人可查看四类业务模板',(await manager.locator('#routineTemplates').innerText()).includes('[例行演练]网络与桌面运维'));
    await manager.evaluate(id=>editRoutineTemplate(id),fixture.templates.ops);
    check('模板每周标准工时可编辑',await manager.locator('#routineHours').isEditable());
    check('模板回显更新后的标准工时',await manager.locator('#routineHours').inputValue()==='20');
    await manager.screenshot({path:path.join(output,'manager-template.png')});
    await manager.locator('#routineForm button[type="button"]').click();
    await manager.locator('#performanceTab').click();
    await manager.waitForFunction(()=>document.querySelector('#performanceContent')?.innerText.includes('绩效'));
    check('负责人绩效页面可正常打开',!(await manager.locator('#performanceContent').innerText()).includes('加载失败'));
    await employee.setViewportSize({width:390,height:844});
    await employee.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
    check('手机页面无横向溢出',await employee.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    check('页面无脚本错误',errors.length===0);
    fixture.checks=fixture.checks.filter(c=>c.detail!=='真实浏览器与账号登录验证');fixture.checks.push(...checks);fixture.browser_checked_at=new Date().toISOString();
    fs.writeFileSync(manifestPath,JSON.stringify(fixture,null,2));
    console.log('PASS:',checks.length,'native-login browser checks; report screenshots retained.');
  }finally{
    for(const p of pages)await p.request.post(base+'/api/auth/logout',{data:{}}).catch(()=>{});
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exit(1)});

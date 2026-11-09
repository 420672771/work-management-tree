// Used only by capture-manual.py against its disposable fictional fixture.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const config=JSON.parse(fs.readFileSync(process.env.WMT_MANUAL_FIXTURE,'utf8'));
assert(config.base.startsWith('http://127.0.0.1:'));
const output=path.resolve(__dirname,'../static/manual-assets');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const errors=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1120},deviceScaleFactor:1});
  const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
  async function shot(name,root='body',marks=[]){
   const area=page.locator(root).first();if(root==='body')await page.evaluate(()=>scrollTo(0,0));else await area.scrollIntoViewIfNeeded();
   await page.evaluate(({marks,root})=>{
    document.querySelector('#handbook-annotations')?.remove();
    const target=document.querySelector(root),isBody=root==='body';target.dataset.capturePosition=target.style.position;target.style.position='relative';
    const layer=document.createElement('div');layer.id='handbook-annotations';layer.style.cssText=`position:${isBody?'fixed':'absolute'};inset:0;pointer-events:none;z-index:2147483647`;
    const bounds=target.getBoundingClientRect(),left=isBody?0:bounds.left,top=isBody?0:bounds.top;
    marks.forEach((selector,i)=>{
     const element=document.querySelector(selector);if(!element)throw Error('Missing screenshot target: '+selector);
     const rect=element.getBoundingClientRect();
     const box=document.createElement('div');box.style.cssText=`position:absolute;left:${rect.left-left-2}px;top:${rect.top-top-2}px;width:${rect.width+4}px;height:${rect.height+4}px;border:2px solid #e65b25;border-radius:5px`;layer.append(box);
     const badge=document.createElement('span');badge.textContent=String(i+1);badge.style.cssText=`position:absolute;left:${Math.max(2,rect.left-left-11)}px;top:${Math.max(3,rect.top-top-12)}px;min-width:25px;height:25px;padding:0 5px;border-radius:15px;background:#d84b16;color:white;font:bold 15px/25px sans-serif;text-align:center;box-shadow:0 1px 4px #0004`;layer.append(badge);
    });
    const note=document.createElement('div');note.textContent='操作示例 · 人员与业务为虚构数据';note.style.cssText='position:absolute;right:8px;bottom:3px;background:#fff9edee;color:#875119;border:1px solid #eed3a6;border-radius:4px;padding:1px 5px;font:10px/16px sans-serif';layer.append(note);
    target.append(layer);
   },{marks,root});
   if(root==='body')await page.screenshot({path:path.join(output,name+'.png')});
   else await area.screenshot({path:path.join(output,name+'.png')});
   await page.evaluate(root=>{document.querySelector('#handbook-annotations')?.remove();const target=document.querySelector(root);target.style.position=target.dataset.capturePosition;delete target.dataset.capturePosition;},root);console.log('Captured '+name);
  }
  async function login(username){
   await page.goto(config.base+'/login');await page.locator('#loginName').fill(username);await page.locator('#loginPassword').fill('123456');
   await page.locator('#loginSubmit').click();await page.waitForURL(config.base+'/');
   await page.waitForFunction(()=>typeof sessionUser!=='undefined'&&sessionUser&&currentUsers.length>0);
  }
  await page.goto(config.base+'/login');await page.locator('#loginName').fill('zhangsan');await page.locator('#loginPassword').fill('123456');
  await shot('screen-login','.login-card',['#loginName','#loginPassword','#loginSubmit','#feishuLogin']);
  await login('zhangsan');
  await shot('screen-home','body',['#tasksTab','#approvalsTab','#monthlyTab','#loginUser']);
  await page.getByRole('button',{name:'个人中心',exact:true}).click();await page.locator('#changePasswordForm').waitFor();
  await shot('screen-profile','#profilePage',['#profileOldPassword','#profileNewPassword','#profileConfirmPassword','#profilePasswordSubmit','#profileBindFeishu']);
  await page.locator('#homeTab').click();
  await page.locator('#taskTitle').fill('完成客户服务流程评审');await page.locator('#owners').selectOption(String(config.member));
  await page.locator('#taskNode').selectOption(String(config.domain));await page.locator('#taskDueAt').fill(config.month+'-28');
  await page.locator('#form details summary').click();
  await page.locator('#form [name="description"]').fill('核对业务流程与异常场景，组织评审并整理纪要。');
  await page.waitForFunction(()=>!document.querySelector('#similarTasks').textContent.includes('正在检查'));
  await shot('screen-task-create','#form',['#taskTitle','#taskNode','#owners','#taskDueAt','#form button[type="submit"],#form .form-actions button']);
  await page.locator('#draftText').fill('请张三在'+config.month+'-28前完成客户服务流程评审，交付评审纪要和问题清单。');
  await shot('screen-ai-input','.ai-assist-section',['#draftText','.draft-attachment-button','.ai-create-card button[onclick="createDraft()"]']);
  await page.evaluate(id=>feedback(id),config.tasks.active);await page.locator('#progressFeedbackDialog').waitFor();
  await page.locator('#progressFeedbackPercent').fill('60');await page.locator('#progressFeedbackComment').fill('已完成主流程核对，待业务确认两项异常规则。');
  await page.locator('#progressFeedbackForm [name="blocked"][value="true"]').check();await page.locator('#progressFeedbackBlockReason').fill('等待业务确认异常处理规则，需要确认人协调。');
  await shot('screen-progress','#progressFeedbackDialog',['#progressFeedbackPercent','#progressFeedbackComment','#progressBlockReasonField','#progressFeedbackSubmit']);
  await page.locator('#progressFeedbackSubmit').click();await page.locator('#progressFeedbackDialog').waitFor({state:'hidden'});
  await page.evaluate(id=>showTaskDetails(id),config.tasks.active);await page.locator('#taskDetailDialog').waitFor();
  await page.getByRole('button',{name:'修改任务',exact:true}).click();await page.locator('#taskEditForm').waitFor();
  await shot('screen-task-edit','#taskEditForm',['#taskEditForm [name="title"]','#taskEditForm [name="owner_id"]','#taskEditForm .task-actions button:first-child']);
  await page.evaluate(()=>document.querySelector('#taskDetailDialog').close());
  await page.evaluate(id=>{void reportRoutineHours(id)},config.routine);await page.locator('#actionInputDialog').waitFor();await page.locator('#actionInputValue').fill('3.5');
  await shot('screen-routine-hours','#actionInputDialog',['#actionInputHint','#actionInputValue','#actionInputForm button[type="submit"]']);
  await page.evaluate(()=>closeActionInput());
  await page.locator('#monthlyTab').click();await page.locator('#monthlyMonth').fill(config.month);await page.locator('#monthlyGenerate').click();
  await page.locator('#monthlyMap [data-node]').first().waitFor();await page.locator('#monthlyMap [data-node="section:summary"]').click();
  await page.locator('[data-map="fit"]').click();
  await shot('screen-monthly','body',['#monthlyMonth','#monthlyActions','#monthlyNodeText','#monthlyNodeActions']);
  await login('manual.manager');
  await page.locator('#approvalsTab').click();await page.locator('#approvals .approval-card').first().waitFor();
  await shot('screen-approvals','#approvalItems',['#approvals .approval-card:first-of-type','#approvals .approval-card:last-of-type']);
  await page.locator('#tasksTab').click();await page.locator('#taskFilter').selectOption('all');
  await page.locator('#taskKeyword').fill('知识库使用指引');await page.waitForTimeout(250);
  await shot('screen-acceptance','#taskPage',['#taskKeyword','#tasks']);
  await page.locator('#orgTab').click();await page.locator('#orgPositionTab').click();
  await page.getByRole('button',{name:'新增成员',exact:true}).click();await page.locator('#memberName').fill('赵六');await page.locator('#memberAccount').fill('zhaoliu');
  await shot('screen-member','#memberDialog',['#memberName','#memberRole','#memberAccount','#memberPassword','#memberForm button[type="submit"]']);
  await page.evaluate(()=>document.querySelector('#memberDialog').close());
  await page.locator('#orgRoutineTab').click();await page.waitForFunction(()=>window.routineTemplates?.length>0);await page.evaluate(id=>editRoutineTemplate(id),config.template);
  await shot('screen-routine-template','#routineDialog',['#routineTitle','#routineOwnerOptions','#routineOwnerSelection','#routineHours','#routineForm button[type="submit"]']);
  await page.evaluate(()=>document.querySelector('#routineDialog').close());
  await page.locator('#orgDomainsTab').click();await page.locator('#domainList table').waitFor();
  await shot('screen-domains','#orgDomains',['#domainCreate','#domainList']);
  await page.locator('[data-domain-edit="'+config.domain+'"]').click();await page.locator('#domainDialog').waitFor();
  await shot('screen-domain-edit','#domainDialog',['#domainName','#domainOwner','#domainBoundary','#domainSave']);
  await page.evaluate(()=>document.querySelector('#domainDialog').close());
  await page.locator('#weeklyTab').click();await page.locator('#weeklyPublish').waitFor();
  await shot('screen-weekly','body',['#weeklyCurrentTab','#weeklyPublish','#weeklySaveStatus','#weeklyActions .weekly-more']);
  assert.deepEqual(errors,[]);console.log('PASS: fictional account-mode handbook screenshots captured without browser errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});

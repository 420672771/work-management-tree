// Use the isolated account/demo fixture described by WMT_ORG_PERMISSION_FIXTURE.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const config=JSON.parse(fs.readFileSync(process.env.WMT_ORG_PERMISSION_FIXTURE,'utf8'));
assert(config.base.startsWith('http://127.0.0.1:'));
(async()=>{
 const browser=await chromium.launch({headless:true}),errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
  page.on('pageerror',error=>errors.push(error.message));let prompts=0;
  page.on('dialog',async dialog=>{prompts++;await dialog.accept()});
  async function roster(){await page.locator('#orgTab').click();await page.locator('#orgPositionTab').click();await page.locator('#positions .org-info-row').first().waitFor();}
  async function login(username){await page.goto(config.base+'/login');await page.locator('#loginName').fill(username);await page.locator('#loginPassword').fill('RoleTest2026!');await page.locator('#loginSubmit').click();await page.waitForURL(config.base+'/');await page.waitForFunction(()=>sessionUser&&currentUsers.length>0);}
  async function readOnly(){
   await page.waitForFunction(()=>document.querySelector('#orgPosition').hasAttribute('data-roster-readonly'));
   assert.equal(await page.locator('#orgPosition .task-actions button:visible').count(),0);
   assert(await page.locator('#rosterPermissionHint').isVisible());
   const before=prompts;
   await page.evaluate(async cfg=>{openPositionDialog();editPosition(cfg.position);openMemberDialog();editMember(cfg.member);await deletePosition(cfg.position);await deleteMember(cfg.member)},config);
   assert.equal(await page.locator('#memberDialog').isVisible(),false);assert.equal(await page.locator('#positionDialog').isVisible(),false);assert.equal(prompts,before);
  }
  if(config.mode==='demo'){
   await page.goto(config.base);await page.waitForFunction(()=>currentUsers.length>0);
   await page.evaluate(id=>setDemoActor(id),config.manager);await roster();
   await page.getByRole('button',{name:'新增岗位',exact:true}).click();assert(await page.locator('#positionDialog').isVisible());
   await page.evaluate(id=>setDemoActor(id),config.member);await readOnly();
   await page.evaluate(id=>setDemoActor(id),config.pm);
   await page.waitForFunction(()=>!document.querySelector('#orgPosition').hasAttribute('data-roster-readonly'));
   await page.getByRole('button',{name:'新增岗位',exact:true}).click();await page.locator('#positionName').fill('演示 PM 权限岗位');await page.locator('#positionForm button[type="submit"]').click();
   await page.locator('#positionDialog').waitFor({state:'hidden'});await page.getByText('演示 PM 权限岗位',{exact:true}).first().waitFor();
   await page.evaluate(id=>setDemoActor(id),config.member);await readOnly();
   console.log('PASS: demo role switching closes management dialogs, restores PM controls and sends the selected actor on position writes');
  }else{
   await login('member.roles');await roster();await readOnly();
   assert((await page.locator('#userPositions').innerText()).includes('Builder Team leader'));
   const denied=await page.evaluate(async cfg=>{
    const requests=[['/api/org/positions',{name:'越权岗位'}],['/api/org/positions/'+cfg.position,{action:'delete'}],['/api/users',{name:'越权成员',role:'manager'}],['/api/users/'+cfg.member+'/manage',{action:'update',name:'越权改名',role:'manager'}],['/api/users/'+cfg.member+'/manage',{action:'delete'}],['/api/org/user-positions',{user_id:cfg.member,position_id:cfg.position}],['/api/users/'+cfg.member+'/reporting-line',{direct_leader_id:cfg.manager}]];
    return Promise.all(requests.map(async([url,body])=>(await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,actor_id:cfg.manager})})).status));
   },config);assert(denied.every(status=>status===403));
   await page.getByRole('button',{name:'个人中心',exact:true}).click();await page.locator('#changePasswordForm').waitFor();
   await login('manager.roles');await roster();
   assert.equal(await page.locator('#rosterPermissionHint').isVisible(),false);
   await page.getByRole('button',{name:'新增岗位',exact:true}).click();await page.locator('#positionName').fill('浏览器权限岗位');await page.locator('#positionForm button[type="submit"]').click();
   await page.locator('#positionDialog').waitFor({state:'hidden'});await page.getByText('浏览器权限岗位',{exact:true}).first().waitFor();
   const position=await page.evaluate(()=>orgRosterPositions.find(row=>row.name==='浏览器权限岗位').id);
   await page.locator('[onclick="editPosition('+position+')"]').click();await page.locator('#positionName').fill('浏览器权限岗位已修改');await page.locator('#positionForm button[type="submit"]').click();await page.locator('#positionDialog').waitFor({state:'hidden'});
   await page.getByRole('button',{name:'新增成员',exact:true}).click();await page.locator('#memberName').fill('浏览器权限成员');await page.locator('#memberAccount').fill('browser.roles');await page.locator('#memberPosition').selectOption(String(position));await page.locator('#memberLeader').selectOption(String(config.manager));await page.locator('#memberForm button[type="submit"]').click();await page.locator('#memberDialog').waitFor({state:'hidden'});
   await page.waitForFunction(()=>currentUsers.some(user=>user.username==='browser.roles'));
   const member=await page.evaluate(()=>currentUsers.find(user=>user.username==='browser.roles').id);
   await login('pm.roles');await roster();
   await page.locator('[onclick="editMember('+member+')"]').click();await page.locator('#memberName').fill('PM 修改后的成员');await page.locator('#memberForm button[type="submit"]').click();await page.locator('#memberDialog').waitFor({state:'hidden'});
   await page.locator('#userPositions').getByText('PM 修改后的成员',{exact:true}).waitFor();
   await page.locator('[onclick="deleteMember('+member+')"]').click();await page.waitForFunction(id=>!currentUsers.some(user=>user.id===id),member);
   await page.locator('[onclick="deletePosition('+position+')"]').click();await page.waitForFunction(id=>!orgRosterPositions.some(row=>row.id===id),position);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   console.log('PASS: employee/team leader read-only roster; direct function and forged API calls blocked; self-service profile retained; manager creates and edits; PM edits and deletes members and positions; mobile layout');
  }
  assert.deepEqual(errors,[]);
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});

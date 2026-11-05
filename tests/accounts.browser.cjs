// Start an isolated account-mode server with admin.browser/AdminBrowser2026! and pm.browser/PmBrowser2026! fixtures.
const {chromium}=require(process.env.WMT_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.WMT_TEST_URL||'http://127.0.0.1:5125';
(async()=>{
 const browser=await chromium.launch();
 try{
  const adminContext=await browser.newContext({viewport:{width:1500,height:1100}}),admin=await adminContext.newPage(),errors=[];
  admin.on('pageerror',e=>errors.push(e.message));admin.on('dialog',dialog=>dialog.accept());
  async function login(page,name,password){await page.goto(base+'/login');await page.locator('#loginName').fill(name);await page.locator('#loginPassword').fill(password);await page.locator('#loginSubmit').click();await page.waitForURL(base+'/');await page.waitForFunction(()=>typeof sessionUser!=='undefined'&&sessionUser);}
  await admin.goto(base);await admin.waitForURL('**/login');
  if(process.env.WMT_ACCOUNT_ARTIFACT_DIR)await admin.screenshot({path:process.env.WMT_ACCOUNT_ARTIFACT_DIR+'/login.png'});
  await login(admin,'admin.browser','AdminBrowser2026!');
  await admin.locator('#orgTab').click();await admin.locator('#orgPosition').waitFor({state:'visible'});
  await admin.getByRole('button',{name:'新增成员',exact:true}).click();await admin.locator('#memberDialog').waitFor({state:'visible'});
  const suffix=Date.now().toString(36),name='浏览器员工 '+suffix,username='staff.'+suffix;
  await admin.locator('#memberName').fill(name);await admin.locator('#memberAccount').fill(username);await admin.locator('#memberPassword').fill('StaffBrowser2026!');
  assert.equal(await admin.locator('#memberRole').inputValue(),'member');await admin.locator('#memberForm button[type="submit"]').click();await admin.locator('#memberDialog').waitFor({state:'hidden'});
  await admin.waitForFunction(name=>currentUsers.some(u=>u.name===name),name);
  const uid=await admin.evaluate(name=>currentUsers.find(u=>u.name===name).id,name);await admin.locator('[data-reset-user="'+uid+'"]').waitFor();
  const staffContext=await browser.newContext({viewport:{width:1500,height:1100}}),staff=await staffContext.newPage();staff.on('pageerror',e=>errors.push(e.message));
  await login(staff,username,'StaffBrowser2026!');await staff.getByRole('button',{name:'个人中心',exact:true}).click();await staff.locator('#profilePage').waitFor({state:'visible'});await staff.locator('#changePasswordForm').waitFor();
  assert(await staff.locator('#profileBindFeishu').isDisabled());
  if(process.env.WMT_ACCOUNT_ARTIFACT_DIR)await staff.screenshot({path:process.env.WMT_ACCOUNT_ARTIFACT_DIR+'/profile.png',fullPage:true});
  await staff.locator('#profileOldPassword').fill('StaffBrowser2026!');await staff.locator('#profileNewPassword').fill('ChangedBrowser2026!');await staff.locator('#profileConfirmPassword').fill('mismatch2026');await staff.locator('#profilePasswordSubmit').click();
  await staff.waitForFunction(()=>document.querySelector('#profileStatus').textContent.includes('不一致'));
  await staff.locator('#profileConfirmPassword').fill('ChangedBrowser2026!');await staff.locator('#profilePasswordSubmit').click();await staff.waitForFunction(()=>document.querySelector('#profileStatus').textContent.includes('密码已更新'));
  await staff.getByRole('button',{name:'退出',exact:true}).click();await staff.waitForURL('**/login');await login(staff,username,'ChangedBrowser2026!');
  await admin.locator('[data-reset-user="'+uid+'"]').click();await admin.locator('#resetNewPassword').fill('AdminReset2026!');await admin.locator('#resetConfirmPassword').fill('AdminReset2026!');await admin.locator('#resetSubmit').click();await admin.locator('#passwordResetDialog').waitFor({state:'hidden'});
  await staff.reload();await staff.waitForURL('**/login');await login(staff,username,'AdminReset2026!');
  // PM has the same reset controls. Employee requests remain denied even with a forged actor_id.
  const result=await staff.evaluate(async uid=>{const response=await fetch('/api/users/'+uid+'/password-reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({new_password:'Forbidden2026!',actor_id:1})});return response.status;},uid);assert.equal(result,403);
  const pm=await browser.newPage();pm.on('dialog',d=>d.accept());await login(pm,'pm.browser','PmBrowser2026!');await pm.locator('#orgTab').click();await pm.locator('[data-reset-user="'+uid+'"]').waitFor();
  await pm.locator('[data-reset-user="'+uid+'"]').click();await pm.locator('#resetNewPassword').fill('PmResetBrowser2026!');await pm.locator('#resetConfirmPassword').fill('PmResetBrowser2026!');await pm.locator('#resetSubmit').click();await pm.locator('#passwordResetDialog').waitFor({state:'hidden'});
  const mobile=await browser.newPage({viewport:{width:390,height:844}});await mobile.goto(base+'/login');assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);console.log('PASS: login page, manager account creation, employee profile/password change, manager and PM resets, session invalidation, employee permission checks, mobile layout');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});

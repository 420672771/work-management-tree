账号界面截图使用独立测试数据库与虚构员工，不含线上账号密码。

- login.png：添加联合利丰 Logo、公司全称后的桌面登录页。
- login-mobile.png：手机端登录页。
- profile.png：个人资料、修改密码及飞书扫码绑定。

浏览器流程验证脚本：tests/accounts.browser.cjs；服务端账号及 OAuth 回调验证：tests/test_accounts.py。
profile.png 来自未配置飞书回调地址的隔离测试服务，因此其中的扫码绑定按钮未启用。登录页截图来自已配置回调的测试站点，不包含登录凭据或员工资料。

公司 Logo 沿用系统原有官网资源：https://www.ufscs.com/Images/logo.png ，保存为 static/brand/unifortune-logo.png，在登录页和工作台共用本地资源。

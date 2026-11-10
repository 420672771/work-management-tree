import http.client
import io
import json
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import patch
import os

import app
import accounts


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=patch.object(app,'DB_PATH',Path(self.temp.name)/'accounts.db');self.path.start()
        self.environment=patch.dict(os.environ,{'WMT_AUTH_MODE':'account','WMT_MONTHLY_DEMO':'0','FEISHU_APP_ID':'cli_test',
            'FEISHU_APP_SECRET':'test-only-secret','WMT_PUBLIC_BASE_URL':'','WMT_FEISHU_OAUTH_REDIRECT_URI':'http://127.0.0.1/auth/feishu/callback'})
        self.environment.start();app.initialize();c=app.db()
        self.manager=c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
        self.pm=c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
        self.member=c.execute("SELECT id FROM user WHERE role='member'").fetchone()[0]
        accounts.configure(c,self.manager,'admin.test','AdminTest2026!',self.manager)
        accounts.configure(c,self.member,'member.test','StaffTest2026!',self.manager);c.commit();c.close()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.environment.stop();self.path.stop();self.temp.cleanup()

    def request(self,path,body=None,jar=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        request_headers={'Content-Type':'application/json'}
        if jar:request_headers['Cookie']='; '.join(k+'='+v for k,v in jar.items())
        request_headers.update(headers or {})
        connection.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,request_headers)
        response=connection.getresponse();raw=response.read();pairs=response.getheaders();status=response.status
        if jar is not None:
            for name,value in pairs:
                if name.lower()=='set-cookie':
                    cookie=SimpleCookie();cookie.load(value)
                    for key,item in cookie.items():
                        if item['max-age']=='0':jar.pop(key,None)
                        else:jar[key]=item.value
        connection.close()
        payload=json.loads(raw) if raw and dict(pairs).get('Content-Type','').startswith('application/json') else raw
        return status,payload,dict(pairs)

    def login(self,name='admin.test',password='AdminTest2026!'):
        jar={};status,_,headers=self.request('/api/auth/login',{'username':name,'password':password},jar)
        self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Lax',headers['Set-Cookie']);return jar

    def oauth(self,jar,bind=False):
        status,_,headers=self.request('/auth/feishu/authorize?flow=bind' if bind else '/auth/feishu/authorize',jar=jar)
        self.assertEqual(status,302)
        query=parse_qs(urlparse(headers['Location']).query)
        self.assertEqual(query['client_id'],['cli_test']);self.assertNotIn('app_id',query)
        return '/auth/feishu/callback?'+urlencode({'state':query['state'][0],'code':'mock-code'})

    def test_password_login_profile_change_and_session_rotation(self):
        member=self.login('MEMBER.TEST','StaffTest2026!');another=self.login('member.test','StaffTest2026!')
        old=member['wmt_session'];self.assertEqual(self.request('/api/me',jar=member)[1]['user']['username'],'member.test')
        self.assertEqual(self.request('/api/me/password',{'current_password':'wrong','new_password':'StaffNew2026!'},member)[0],403)
        self.assertEqual(self.request('/api/me/password',{'current_password':'StaffTest2026!','new_password':'StaffNew2026!'},member)[0],200)
        self.assertNotEqual(old,member['wmt_session']);self.assertEqual(self.request('/api/me',jar=another)[0],401)
        self.assertEqual(self.request('/api/auth/login',{'username':'member.test','password':'StaffTest2026!'})[0],401)
        self.login('member.test','StaffNew2026!')
        self.assertEqual(self.request('/api/auth/logout',{},member)[0],200);self.assertEqual(self.request('/api/me',jar=member)[0],401)

    def test_manager_and_pm_reset_member_cannot_escalate(self):
        manager=self.login();member=self.login('member.test','StaffTest2026!')
        self.assertEqual(self.request('/api/users/'+str(self.manager)+'/password-reset',{'new_password':'Stolen2026!','actor_id':self.manager},member)[0],403)
        self.assertEqual(self.request('/api/users/'+str(self.member)+'/manage',{'action':'update','name':'恶意提权','role':'manager'},member)[0],403)
        self.assertEqual(self.request('/api/users',{'name':'越权创建','username':'evil','password':'Stolen2026!'},member)[0],403)
        self.assertEqual(self.request('/api/users/'+str(self.member)+'/password-reset',{'new_password':'ResetNew2026!'},manager)[0],200)
        self.assertEqual(self.request('/api/me',jar=member)[0],401);self.login('member.test','ResetNew2026!')
        c=app.db();accounts.configure(c,self.pm,'pm.test','PmTest2026!',self.manager);c.commit();c.close()
        pm=self.login('pm.test','PmTest2026!')
        self.assertEqual(self.request('/api/users/'+str(self.member)+'/password-reset',{'new_password':'PmReset2026!'},pm)[0],200)
        self.login('member.test','PmReset2026!')

    def test_account_creation_is_atomic_and_never_exposes_hash_or_password(self):
        manager=self.login();body={'name':'账号测试员工','role':'member','username':'unique.test','password':'SecretFixture2026!'}
        status,result,_=self.request('/api/users',body,manager);self.assertEqual(status,200)
        uid=result['id'];c=app.db();count=c.execute('SELECT COUNT(*) FROM user').fetchone()[0]
        self.assertEqual(self.request('/api/users',{**body,'username':'UNIQUE.TEST'},manager)[0],409)
        self.assertEqual(self.request('/api/users',{**body,'username':'other.test','password':'123'},manager)[0],400)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM user').fetchone()[0],count)
        digest=c.execute('SELECT password_hash FROM user_account WHERE user_id=?',(uid,)).fetchone()[0]
        self.assertTrue(digest.startswith('pbkdf2_sha256$600000$'));self.assertNotIn(body['password'],digest)
        audit=' '.join(str(r[0]) for r in c.execute('SELECT after_json FROM audit_log'))
        self.assertNotIn(body['password'],audit);c.close()
        for endpoint in ('/api/users','/api/bootstrap','/api/session','/api/me'):
            data=json.dumps(self.request(endpoint,jar=manager)[1]);self.assertNotIn('password_hash',data);self.assertNotIn(body['password'],data)
        self.assertEqual(self.request('/api/users/'+str(uid)+'/manage',{'action':'delete'},manager)[0],200)
        self.assertEqual(self.request('/api/auth/login',{'username':'unique.test','password':body['password']})[0],401)

    def test_throttling_reset_and_origin_validation(self):
        for _ in range(5):self.assertEqual(self.request('/api/auth/login',{'username':'member.test','password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/auth/login',{'username':'member.test','password':'StaffTest2026!'})[0],429)
        manager=self.login();self.assertEqual(self.request('/api/users/'+str(self.member)+'/password-reset',{'new_password':'Unlocked2026!'},manager)[0],200)
        self.login('member.test','Unlocked2026!')
        self.assertEqual(self.request('/api/auth/login',{'username':'member.test','password':'Unlocked2026!'},headers={'Origin':'https://outside.example'})[0],403)
        self.assertEqual(self.request('/api/auth/login',{},headers={'Content-Type':'text/plain'})[0],400)

    def test_demo_manager_can_configure_and_reset_but_demo_employee_cannot(self):
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'1'}):
            status,created,_=self.request('/api/users',{'name':'演示账号','username':'demo.account','password':'DemoTest2026!','actor_id':self.pm})
            self.assertEqual(status,200)
            self.assertEqual(self.request('/api/users/'+str(created['id'])+'/password-reset',{'new_password':'NewDemo2026!','actor_id':self.member})[0],403)
            self.assertEqual(self.request('/api/users/'+str(created['id'])+'/password-reset',{'new_password':'NewDemo2026!','actor_id':self.manager})[0],200)
            self.login('demo.account','NewDemo2026!')

    def test_roster_employee_and_team_leader_cannot_write_or_forge_management_identity(self):
        member=self.login('member.test','StaffTest2026!')
        c=app.db()
        position=c.execute("INSERT INTO org_position(name) VALUES('Builder Team leader')").lastrowid
        c.execute('INSERT INTO user_position(user_id,position_id) VALUES(?,?)',(self.member,position))
        line=c.execute('SELECT id FROM reporting_line WHERE user_id=? AND effective_to IS NULL',(self.member,)).fetchone()[0]
        c.commit()
        tables=('user','user_account','org_position','user_position','reporting_line','feishu_binding_history','audit_log')
        before={table:[tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY 1')] for table in tables}
        writes=[
            ('/api/users',{'name':'越权新增','role':'manager'}),
            (f'/api/users/{self.member}/manage',{'action':'update','name':'越权改名','role':'manager'}),
            (f'/api/users/{self.member}/manage',{'action':'delete'}),
            ('/api/org/positions',{'name':'越权新增岗位'}),
            (f'/api/org/positions/{position}',{'action':'update','name':'越权岗位'}),
            (f'/api/org/positions/{position}',{'action':'delete'}),
            ('/api/org/user-positions',{'user_id':self.member,'position_id':position,'is_primary':True}),
            (f'/api/users/{self.member}/reporting-line',{'direct_leader_id':self.pm}),
            (f'/api/users/{self.member}/reporting-line',{'direct_leader_id':None}),
            ('/api/reporting-lines',{'user_id':self.member,'direct_leader_id':self.pm}),
            (f'/api/reporting-lines/{line}',{'action':'update','user_id':self.member,'direct_leader_id':self.pm}),
            (f'/api/reporting-lines/{line}',{'action':'delete'}),
            (f'/api/users/{self.member}/feishu-open-id',{'feishu_open_id':'ou_forbidden'}),
            (f'/api/users/{self.member}/feishu-open-id/unbind',{}),
        ]
        for path,body in writes:
            for identity in ({},{'actor_id':self.manager,'operator_id':self.manager,'user_id':self.manager}):
                with self.subTest(path=path,body=body,forged=bool(identity)):
                    status,result,_=self.request(path,{**body,**identity},member)
                    self.assertEqual(status,403);self.assertIn('仅部门负责人和管理 PM',result['error'])
        for table in tables:self.assertEqual(before[table],[tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY 1')],table)
        c.close()
        for path in ('/api/users','/api/org/positions','/api/org/user-positions','/api/reporting-lines'):
            self.assertEqual(self.request(path,jar=member)[0],200)
        self.assertEqual(self.request('/api/org/positions',{'name':'未登录新增'})[0],401)

    def test_roster_manager_and_pm_can_create_update_assign_and_delete(self):
        c=app.db();accounts.configure(c,self.pm,'pm.test','PmTest2026!',self.manager);c.commit();c.close()
        for name,password in (('admin.test','AdminTest2026!'),('pm.test','PmTest2026!')):
            jar=self.login(name,password)
            with self.subTest(actor=name):
                status,position,_=self.request('/api/org/positions',{'name':'授权维护岗位'},jar);self.assertEqual(status,200);pid=position['id']
                self.assertEqual(self.request(f'/api/org/positions/{pid}',{'action':'update','name':'已修改岗位','level':'P6'},jar)[0],200)
                status,user,_=self.request('/api/users',{'name':'授权维护成员','role':'member'},jar);self.assertEqual(status,200);uid=user['id']
                self.assertEqual(self.request(f'/api/users/{uid}/manage',{'action':'update','name':'已修改成员','role':'member'},jar)[0],200)
                self.assertEqual(self.request('/api/org/user-positions',{'user_id':uid,'position_id':pid},jar)[0],200)
                self.assertEqual(self.request(f'/api/users/{uid}/reporting-line',{'direct_leader_id':self.manager},jar)[0],200)
                self.assertEqual(self.request(f'/api/users/{uid}/reporting-line',{'direct_leader_id':None},jar)[0],200)
                status,line,_=self.request('/api/reporting-lines',{'user_id':uid,'direct_leader_id':self.manager},jar);self.assertEqual(status,200)
                status,updated,_=self.request('/api/reporting-lines/'+str(line['id']),{'action':'update','user_id':uid,'direct_leader_id':self.manager},jar);self.assertEqual(status,200)
                self.assertEqual(self.request('/api/reporting-lines/'+str(updated['id']),{'action':'delete'},jar)[0],200)
                self.assertEqual(self.request(f'/api/org/positions/{pid}',{'action':'delete'},jar)[0],200)
                self.assertEqual(self.request(f'/api/users/{uid}/manage',{'action':'delete'},jar)[0],200)
                c=app.db();self.assertIsNone(c.execute('SELECT id FROM user WHERE id=?',(uid,)).fetchone())
                self.assertEqual(c.execute('SELECT status FROM org_position WHERE id=?',(pid,)).fetchone()[0],'inactive');c.close()

    def test_roster_demo_has_no_unidentified_write_and_authenticated_identity_wins(self):
        member=self.login('member.test','StaffTest2026!')
        with patch.dict(os.environ,{'WMT_AUTH_MODE':'demo','WMT_MONTHLY_DEMO':'1'}):
            for actor in (None,self.member):
                body={'name':'越权岗位'}
                if actor:body['actor_id']=actor
                self.assertEqual(self.request('/api/org/positions',body)[0],403)
                self.assertEqual(self.request('/api/users',{**body,'role':'member'})[0],403)
            self.assertEqual(self.request('/api/org/positions',{'name':'伪造管理身份','actor_id':self.manager},member)[0],403)
            self.assertEqual(self.request('/api/org/positions',{'name':'演示管理者岗位','actor_id':self.manager})[0],200)
            self.assertEqual(self.request('/api/users',{'name':'演示 PM 成员','role':'member','actor_id':self.pm})[0],200)

    def test_roster_role_revocation_applies_to_an_existing_login_session(self):
        manager=self.login()
        c=app.db();c.execute("UPDATE user SET role='member' WHERE id=?",(self.manager,));c.commit();c.close()
        self.assertEqual(self.request('/api/org/positions',{'name':'旧管理者不能新增','actor_id':self.pm},manager)[0],403)
        self.assertEqual(self.request('/api/users',{'name':'旧管理者不能新增','role':'member'},manager)[0],403)

    def test_bind_then_feishu_login_and_replayed_callback_rejected(self):
        member=self.login('member.test','StaffTest2026!');callback=self.oauth(member,bind=True)
        with patch.object(app.FeishuAdapter,'exchange_login_code',return_value={'access_token':'test-token'}),patch.object(app.FeishuAdapter,'login_open_id',return_value='ou_test_bound'):
            self.assertIn('binding=success',self.request(callback,jar=member)[2]['Location'])
            self.assertIn('expired',self.request(callback,jar=member)[2]['Location'])
            self.assertTrue(self.request('/api/me',jar=member)[1]['feishu_bound'])
            fresh={};login_callback=self.oauth(fresh)
            self.assertEqual(self.request(login_callback,jar=fresh)[2]['Location'],'/')
            self.assertEqual(self.request('/api/me',jar=fresh)[1]['user']['id'],self.member)

    def test_bind_rejects_wrong_browser_and_changed_session(self):
        member=self.login('member.test','StaffTest2026!');callback=self.oauth(member,bind=True)
        with patch.object(app.FeishuAdapter,'exchange_login_code') as exchange:
            self.assertIn('expired',self.request(callback,jar={})[2]['Location']);exchange.assert_not_called()
            # Same browser's OAuth cookie with another/new login session is insufficient.
            other=self.login();other['wmt_oauth_state']=member['wmt_oauth_state']
            self.assertIn('expired',self.request(callback,jar=other)[2]['Location']);exchange.assert_not_called()
        self.assertFalse(self.request('/api/me',jar=member)[1]['feishu_bound'])

    def test_bind_collision_and_unbound_login_do_not_change_accounts(self):
        c=app.db();c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',('ou_taken',self.manager));c.commit();c.close()
        member=self.login('member.test','StaffTest2026!');callback=self.oauth(member,bind=True)
        with patch.object(app.FeishuAdapter,'exchange_login_code',return_value={'access_token':'test-token'}),patch.object(app.FeishuAdapter,'login_open_id',return_value='ou_taken'):
            self.assertIn('already_bound',self.request(callback,jar=member)[2]['Location'])
        self.assertFalse(self.request('/api/me',jar=member)[1]['feishu_bound'])
        fresh={};callback=self.oauth(fresh)
        with patch.object(app.FeishuAdapter,'exchange_login_code',return_value={'access_token':'test-token'}),patch.object(app.FeishuAdapter,'login_open_id',return_value='ou_unbound'):
            self.assertIn('unbound',self.request(callback,jar=fresh)[2]['Location'])
        self.assertNotIn('wmt_session',fresh)

    def test_disabled_account_mode_requires_login_and_public_login_page_available(self):
        self.assertEqual(self.request('/login')[0],200);self.assertEqual(self.request('/api/users')[0],401)
        self.assertEqual(self.request('/api/me')[0],401)
        self.assertEqual(self.request('/api/me/password',{'current_password':'x','new_password':'password'})[0],401)
        options=self.request('/api/auth/options')[1];self.assertFalse(options['demo_enabled']);self.assertTrue(options['feishu_ready'])
        with patch.dict(os.environ,{'WMT_FEISHU_OAUTH_REDIRECT_URI':'','WMT_PUBLIC_BASE_URL':''}):
            self.assertFalse(self.request('/api/auth/options')[1]['feishu_ready'])
            self.assertIn('not_configured',self.request('/auth/feishu/start')[2]['Location'])

    def test_qr_binding_and_login_use_the_same_verified_callback(self):
        member=self.login('member.test','StaffTest2026!')
        for flow,jar in (('bind',member),('login',{})):
            status,data,headers=self.request('/auth/feishu/qr?flow='+flow,jar=jar)
            self.assertEqual(status,200);self.assertEqual(data['expires_in'],600)
            self.assertNotIn('test-only-secret',json.dumps(data));self.assertIn('no-store',headers['Cache-Control'])
            cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('Path=/auth/feishu',cookie)
            url=urlparse(data['authorization_url']);self.assertEqual(url.netloc,'passport.feishu.cn')
            query=parse_qs(url.query);self.assertEqual(query['client_id'],['cli_test'])
            self.assertEqual(query['state'],[jar['wmt_oauth_state']])
            c=app.db();row=c.execute('SELECT * FROM login_state WHERE state_hash=?',(app.hashlib.sha256(jar['wmt_oauth_state'].encode()).hexdigest(),)).fetchone();c.close()
            self.assertEqual(row['flow_type'],'qr_'+flow);self.assertEqual(row['user_id'],self.member if flow=='bind' else None)
            callback='/auth/feishu/callback?'+urlencode({'state':query['state'][0],'code':'mock-scanned-code'})
            with patch.object(app.FeishuAdapter,'exchange_qr_login_code',return_value={'access_token':'test-token'}),patch.object(app.FeishuAdapter,'qr_login_open_id',return_value='ou_qr_bound'):
                result=self.request(callback,jar=jar)[2]['Location']
                self.assertEqual(result,'/?page=profile&binding=success' if flow=='bind' else '/')
                self.assertIn('expired',self.request(callback,jar=jar)[2]['Location'])
            self.assertEqual(self.request('/api/me',jar=jar)[1]['user']['id'],self.member)

    def test_qr_refresh_invalidates_previous_state_and_cannot_cross_browser(self):
        jar={};_,first,_=self.request('/auth/feishu/qr',jar=jar);old=jar.copy()
        _,second,_=self.request('/auth/feishu/qr',jar=jar)
        self.assertNotEqual(first['authorization_url'],second['authorization_url'])
        c=app.db();self.assertEqual(c.execute('SELECT COUNT(*) FROM login_state').fetchone()[0],1);c.close()
        with patch.object(app.FeishuAdapter,'exchange_qr_login_code') as exchange:
            callback='/auth/feishu/callback?'+urlencode({'state':old['wmt_oauth_state'],'code':'old-code'})
            self.assertIn('expired',self.request(callback,jar=old)[2]['Location'])
            callback='/auth/feishu/callback?'+urlencode({'state':jar['wmt_oauth_state'],'code':'wrong-browser-code'})
            self.assertIn('expired',self.request(callback,jar={})[2]['Location']);exchange.assert_not_called()

    def test_qr_binding_rejects_changed_session_before_exchanging_code(self):
        member=self.login('member.test','StaffTest2026!')
        self.request('/auth/feishu/qr?flow=bind',jar=member)
        other=self.login();other['wmt_oauth_state']=member['wmt_oauth_state']
        callback='/auth/feishu/callback?'+urlencode({'state':member['wmt_oauth_state'],'code':'mock-scanned-code'})
        with patch.object(app.FeishuAdapter,'exchange_qr_login_code') as exchange:
            self.assertIn('expired',self.request(callback,jar=other)[2]['Location']);exchange.assert_not_called()

    def test_qr_code_exchange_uses_the_sdk_supported_earlier_oauth_protocol(self):
        response=io.BytesIO(b'{"access_token":"fixture-user-token"}')
        with patch.object(app.urllib.request,'urlopen',return_value=response) as request:
            self.assertEqual(app.FeishuAdapter().exchange_qr_login_code('fixture-code')['access_token'],'fixture-user-token')
        token_request=request.call_args.args[0]
        self.assertEqual(token_request.full_url,'https://passport.feishu.cn/suite/passport/oauth/token')
        self.assertEqual(token_request.get_header('Content-type'),'application/x-www-form-urlencoded')
        payload=parse_qs(token_request.data.decode())
        self.assertEqual(payload['client_id'],['cli_test']);self.assertEqual(payload['code'],['fixture-code'])
        self.assertEqual(payload['client_secret'],['test-only-secret'])
        self.assertEqual(payload['redirect_uri'],['http://127.0.0.1/auth/feishu/callback'])
        with patch.object(app.urllib.request,'urlopen',return_value=io.BytesIO(b'{"error":"invalid_grant"}')):
            with self.assertRaises(RuntimeError):app.FeishuAdapter().exchange_qr_login_code('fixture-code')
        with patch.object(app.urllib.request,'urlopen',return_value=io.BytesIO(b'{"sub":"ou_fixture","open_id":"ou_fixture"}')) as request:
            self.assertEqual(app.FeishuAdapter().qr_login_open_id('fixture-user-token'),'ou_fixture')
        self.assertEqual(request.call_args.args[0].full_url,'https://passport.feishu.cn/suite/passport/oauth/userinfo')
        self.assertEqual(request.call_args.args[0].get_header('Authorization'),'Bearer fixture-user-token')
        with patch.object(app.urllib.request,'urlopen',return_value=io.BytesIO(b'{"error":"invalid_token"}')):
            with self.assertRaises(RuntimeError):app.FeishuAdapter().qr_login_open_id('fixture-user-token')

    def test_qr_requires_binding_session_and_valid_configuration(self):
        self.assertEqual(self.request('/static/feishu-login.js')[0],200)
        self.assertEqual(self.request('/auth/feishu/qr?flow=bind')[0],401)
        self.assertEqual(self.request('/auth/feishu/qr?flow=invalid')[0],400)
        with patch.dict(os.environ,{'FEISHU_APP_SECRET':''}):
            self.assertEqual(self.request('/auth/feishu/qr')[0],503)
        c=app.db();self.assertEqual(c.execute('SELECT COUNT(*) FROM login_state').fetchone()[0],0);c.close()
        with patch.dict(os.environ,{'WMT_FEISHU_OAUTH_REDIRECT_URI':'https://work.example.com/auth/feishu/callback'}):
            self.assertIn('Secure',self.request('/auth/feishu/qr')[2]['Set-Cookie'])

    def test_legacy_entry_urls_show_qr_page_instead_of_authorization_redirect(self):
        status,html,headers=self.request('/auth/feishu/start')
        self.assertEqual(status,200);self.assertNotIn('Location',headers)
        self.assertIn(b'openFeishuQr(entryFlow)',html);self.assertIn(b'/static/feishu-login.js',html)
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertEqual(self.request('/auth/feishu/bind')[2]['Location'],'/login')
        member=self.login('member.test','StaffTest2026!')
        self.assertEqual(self.request('/auth/feishu/bind',jar=member)[0],200)
        self.assertEqual(self.request('/auth/feishu/authorize?flow=invalid')[0],400)
        c=app.db();self.assertEqual(c.execute('SELECT COUNT(*) FROM login_state').fetchone()[0],0);c.close()

    def test_hashes_use_independent_salts(self):
        first=accounts.hash_password('SameTest2026!');second=accounts.hash_password('SameTest2026!')
        self.assertNotEqual(first,second);self.assertTrue(accounts.verify_password('SameTest2026!',first));self.assertFalse(accounts.verify_password('wrong',first))

    def test_new_accounts_use_initial_password_and_existing_passwords_remain_unchanged(self):
        manager=self.login()
        status,created,_=self.request('/api/users',{'name':'默认密码测试员','role':'member','username':'default.staff'},manager)
        self.assertEqual(status,200)
        staff=self.login('default.staff','123456')
        self.assertEqual(self.request('/api/me',jar=staff)[1]['user']['id'],created['id'])
        self.assertEqual(self.request('/api/me/password',{'current_password':'123456','new_password':'PersonalNew2026!'},staff)[0],200)
        self.assertEqual(self.request('/api/users/'+str(created['id'])+'/manage',{'action':'update','name':'默认密码测试员','role':'member','username':'default.staff','password':''},manager)[0],200)
        self.assertEqual(self.request('/api/me',jar=staff)[0],200)
        self.login('default.staff','PersonalNew2026!')
        self.assertEqual(self.request('/api/auth/login',{'username':'default.staff','password':'123456'})[0],401)
        # First-time account configuration for an existing employee also gets the default.
        c=app.db();other=c.execute("SELECT id,name FROM user WHERE role='member' AND id<>? LIMIT 1",(self.member,)).fetchone();c.close()
        self.assertEqual(self.request('/api/users/'+str(other['id'])+'/manage',{'action':'update','name':other['name'],'role':'member','username':'default.existing','password':''},manager)[0],200)
        self.login('default.existing','123456')
        self.assertEqual(self.request('/api/users/'+str(created['id'])+'/password-reset',{'new_password':'123456'},manager)[0],200)
        self.login('default.staff','123456')
        with self.assertRaises(ValueError):accounts.hash_password('12345')

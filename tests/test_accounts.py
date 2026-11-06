import http.client
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
        status,_,headers=self.request('/auth/feishu/bind' if bind else '/auth/feishu/start',jar=jar)
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

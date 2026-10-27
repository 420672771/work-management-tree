import base64, json, os, struct, tempfile, threading, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
os.environ['WMT_DB']=str(Path(tempfile.mkdtemp())/'test.db')
import sys; sys.path.insert(0,str(Path(__file__).parents[1]))
import app

class WorkTreeTests(unittest.TestCase):
    def setUp(self): app.initialize()
    def test_seed_and_ai_draft_requires_confirmation(self):
        c=app.db(); self.assertGreater(c.execute('select count(*) from org_node').fetchone()[0],0); c.close()
        x=app.ai_draft('请在2026-10-20前完成产品方案'); self.assertTrue(x['requires_human_confirmation']); self.assertEqual(x['due_at'][:10],'2026-10-20')
    def test_task_requires_owner_and_deliverable(self):
        c=app.db();
        try:
            with self.assertRaises(Exception): c.execute('insert into task(title,owner_id,due_at) values(?,?,?)',('x',1,'2026-10-20'))
        finally: c.close()
    def test_time_status(self):
        c=app.db(); c.execute("insert into task(title,owner_id,due_at,deliverable,created_at,updated_at) values(?,?,?,?,?,?)",('late',1,'2020-01-01T00:00:00+00:00','x',app.now(),app.now())); c.commit(); x=app.task_view(c,c.execute("select * from task where title='late' order by id desc").fetchone()); self.assertEqual(x['time_status'],'overdue'); c.close()
    def test_wecom_is_safe_without_credentials(self):
        for key in ('WECOM_CORP_ID','WECOM_AGENT_ID','WECOM_SECRET'): os.environ.pop(key, None)
        result=app.WeComAdapter().send('tester','test message')
        self.assertEqual(result['mode'],'dry-run')
        self.assertFalse(result['sent'])
    def test_conversational_progress_resolves_only_a_single_active_task(self):
        c=app.db()
        try:
            owner=c.execute("select id from user where role<>'manager' limit 1").fetchone()[0]
            manager=c.execute("select id from user where role='manager' limit 1").fetchone()[0]
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,approver_id,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('唯一待办',owner,'2030-01-01T17:00:00+08:00','验收记录','approved',manager,app.now(),app.now())).lastrowid
            c.commit()
            progress,error=app.resolve_progress_message(c,'此任务已完成80%',owner)
            self.assertIsNone(error); self.assertEqual(progress['task_id'],tid); self.assertEqual(progress['percent'],80)
            self.assertFalse(app.is_task_creation_request('此任务已完成80%'))
            self.assertTrue(app.is_task_creation_request('请张晨负责于2030年1月2日前完成需求评审'))
            self.assertIn('我的待办',app.feishu_task_list(c,owner))
            self.assertTrue(app.is_task_query_request('我都有哪些任务'))
            self.assertTrue(app.is_task_query_request('我有什么待办'))
            self.assertTrue(app.is_task_query_request('今天有哪些任务'))
            self.assertTrue(app.is_task_query_request('我今天要做什么'))
            self.assertTrue(app.is_task_query_request('查一下我手上的工作'))
            self.assertTrue(app.is_task_query_request('查看我负责的事项'))
            self.assertTrue(app.is_task_query_request('我现在有什么安排'))
            self.assertFalse(app.is_task_query_request('任务#15 完成80%'))
            self.assertEqual(app.classify_bot_intent('我今天要做什么'),'task_query')
            self.assertEqual(app.classify_bot_intent('你能做什么？'),'help')
            self.assertEqual(app.rule_bot_intent('我现在有什么安排'),'task_query')
        finally: c.close()
    def test_bot_sensitive_actions_require_a_plan_before_execution(self):
        c=app.db()
        try:
            pm=c.execute("select id from user where role='pm' limit 1").fetchone()[0]
            owner=c.execute("select id from user where role<>'manager' and id<>? limit 1",(pm,)).fetchone()[0]
            target=c.execute("select * from user where id<>? limit 1",(owner,)).fetchone()
            manager=c.execute("select id from user where role='manager' limit 1").fetchone()[0]
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,approver_id,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('机器人变更',owner,'2030-01-01T17:00:00+08:00','记录','approved',manager,app.now(),app.now())).lastrowid; c.commit()
            action,reply=app.plan_bot_action(c,f'任务#{tid} 责任人改为{target["name"]}',pm,'feishu:test')
            self.assertEqual(action['type'],'change_owner'); self.assertIn('回复“确认”',reply)
            self.assertEqual(c.execute("select status from bot_pending_action where session_id='feishu:test'").fetchone()[0],'pending')
            self.assertEqual(app.execute_bot_action(c,action,pm),'任务责任人已变更。')
            self.assertEqual(c.execute('select owner_id from task where id=?',(tid,)).fetchone()[0],target['id'])
        finally: c.close()
    def test_task_view_preserves_separate_risk_and_time_states(self):
        c=app.db()
        try:
            c.execute("insert into task(title,owner_id,due_at,deliverable,risk_status,created_at,updated_at) values(?,?,?,?,?,?,?)",('blocked',1,'2020-01-01T00:00:00+00:00','x','blocked',app.now(),app.now())); c.commit()
            x=app.task_view(c,c.execute("select * from task where title='blocked'").fetchone())
            self.assertEqual(x['risk_status'],'blocked')
            self.assertEqual(x['time_status'],'overdue')
        finally: c.close()
    def test_v2_reporting_line_and_progress_confirmation_models(self):
        c=app.db()
        try:
            manager=c.execute("select id from user where role='manager' limit 1").fetchone()[0]
            owner=c.execute("select id from user where id<>? limit 1",(manager,)).fetchone()[0]
            self.assertEqual(app.approver_for(c,owner),manager)
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,approver_id,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('v2',owner,'2030-01-01T00:00:00+00:00','x','approved',manager,app.now(),app.now())).lastrowid
            rid=c.execute("insert into progress_record(task_id,reported_percent,report_text,reported_at) values(?,?,?,?)",(tid,60,'完成核心流程',app.now())).lastrowid
            aid=app.create_approval(c,'progress_confirm',rid,tid,manager); c.commit()
            self.assertEqual(c.execute('select status from approval_record where id=?',(aid,)).fetchone()[0],'pending')
            self.assertEqual(c.execute('select effective_percent from progress_record where id=?',(rid,)).fetchone()[0],None)
        finally: c.close()
    def test_routine_generation_is_idempotent(self):
        c=app.db()
        try:
            owner=2; node=c.execute("select id from org_node where node_type='domain' limit 1").fetchone()[0]
            c.execute("insert into task_template(org_node_id,title,owner_id,deliverable,recurrence,created_at) values(?,?,?,?,?,?)",(node,'每日站会',owner,'站会纪要','DAILY',app.now()))
            self.assertEqual(len(app.generate_routines(c,'2030-01-01')),1)
            self.assertEqual(len(app.generate_routines(c,'2030-01-01')),0)
            self.assertEqual(c.execute("select count(*) from task where task_type='ROUTINE'").fetchone()[0],1)
        finally: c.close()
    def test_responsibility_domain_bootstrap_assigns_five_domains_and_owners(self):
        c=app.db()
        try:
            rows={row['domain_code']:row for row in c.execute("select domain_code,name,operating_line,owner_id from org_node where domain_code<>''")}
            expected={'A1':('Agent 应用交付域','AI创新线','钱缘'),'A2':('AI 平台与技术能力域','AI创新线','闫兴赫'),'A3':('AI 推广与赋能运营域','AI创新线','覃俞淦'),'B1':('核心业务系统运营保障域','运营保障线','钱缘'),'B2':('IT 基础设施与终端运维域','运营保障线','封滘生')}
            self.assertEqual(set(rows),set(expected))
            for code,(name,line,owner) in expected.items():
                self.assertEqual(rows[code]['name'],name); self.assertEqual(rows[code]['operating_line'],line)
                self.assertEqual(c.execute('select name from user where id=?',(rows[code]['owner_id'],)).fetchone()[0],owner)
        finally: c.close()
    def test_daily_jobs_mark_unreported_without_duplicate_failures(self):
        c=app.db()
        try:
            c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,work_status,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('日报验收',2,'2030-01-01T00:00:00+00:00','日报','approved','in_progress',app.now(),app.now()))
            result=app.run_daily_jobs(c,'2030-01-01','all')
            self.assertGreaterEqual(result['morning'],1)
            self.assertGreaterEqual(result['redboard'],1)
            self.assertGreater(c.execute("select count(*) from daily_report_status where report_date='2030-01-01'").fetchone()[0],0)
        finally: c.close()
    def test_conversation_draft_slots_require_owner_and_deadline(self):
        slots,missing=app.draft_slots('请完成会议纪要')
        self.assertIn('owner_id',missing)
        self.assertIn('due_at',missing)
        slots,missing=app.draft_slots('请在2030-01-01完成会议纪要',2)
        self.assertFalse(missing)
        self.assertEqual(slots['due_at'][:10],'2030-01-01')
    def test_draft_slots_detect_responsibility_domain_or_leaves_ambiguous_unset(self):
        c=app.db()
        try:
            slots,_=app.draft_slots('请在2030-01-01前完成 ERP 供应链结算异常处理方案',2,c)
            self.assertEqual(c.execute('select domain_code from org_node where id=?',(slots['org_node_id'],)).fetchone()[0],'B1')
            slots,_=app.draft_slots('请在2030-01-01前完成网络设备巡检和机房整改',2,c)
            self.assertEqual(c.execute('select domain_code from org_node where id=?',(slots['org_node_id'],)).fetchone()[0],'B2')
            slots,_=app.draft_slots('请在2030-01-01前完成会议事项跟进',2,c)
            self.assertIsNone(slots['org_node_id'])
            self.assertEqual(slots['domain_name'],'')
        finally: c.close()
    def test_parse_due_understands_chinese_weekday(self):
        from datetime import datetime
        due=app.parse_due('本周四之前完成部门总结报告')
        self.assertIsNotNone(due)
        self.assertEqual(datetime.fromisoformat(due).weekday(),3)
    def test_parse_due_understands_spaced_chinese_date_in_meeting_notes(self):
        text='请钱缘负责完成采购审批 Agent 的方案评审，并于 2026 年 10 月 30 日前提交上线验证记录。'
        self.assertEqual(app.parse_due(text),'2026-10-30T17:00:00+08:00')
        slots,missing=app.draft_slots(text,2)
        self.assertEqual(slots['due_at'],'2026-10-30T17:00:00+08:00')
        self.assertNotIn('due_at',missing)
    def test_ai_draft_splits_concise_title_from_meeting_note_detail(self):
        text='【科技产品中心周例会纪要】业务部门提出，希望在采购审批场景上线智能问答助手。请钱缘负责完成采购审批 Agent 的方案评审、测试环境部署及业务试用支持，并于 2026 年 10 月 30 日前提交上线验证记录和业务反馈汇总。'
        draft=app.ai_draft(text)
        self.assertEqual(draft['title'],'采购审批 Agent 的方案评审、测试环境部署及业务试用支持')
        self.assertEqual(draft['description'],'采购审批 Agent 的方案评审、测试环境部署及业务试用支持')
        self.assertLess(len(draft['title']),len(text))
        c=app.db()
        money=c.execute("select id from user where name='钱缘'").fetchone()[0]
        slots,_=app.draft_slots(text,2,c)
        c.close()
        self.assertEqual(slots['title'],draft['title'])
        self.assertEqual(slots['description'],draft['description'])
        self.assertEqual(slots['owner_id'],money)
    def test_ai_draft_filters_irrelevant_meeting_background(self):
        text='本周会议先通报前三季度经营数据、客户投诉和行业政策变化，以上内容仅供知悉。请闫兴赫负责完成知识库检索能力评估，并于2026年11月5日前提交评估结论。其他议题下周继续讨论。'
        draft=app.ai_draft(text)
        self.assertEqual(draft['title'],'知识库检索能力评估')
        self.assertEqual(draft['description'],'知识库检索能力评估')
        self.assertNotIn('经营数据',draft['title']+draft['description'])
        self.assertNotIn('闫兴赫',draft['description'])
        self.assertNotIn('2026',draft['description'])
    def test_friendly_slot_labels_never_returns_internal_names(self):
        labels=app.friendly_slot_labels(['owner_id','due_at','unexpected_key'])
        self.assertEqual(labels,'任务责任人、截止日期、必要信息')
        self.assertNotIn('owner_id',labels)
        self.assertNotIn('due_at',labels)
    def test_discard_draft_hides_it_but_keeps_audit_record(self):
        c=app.db()
        try:
            did=c.execute("insert into conversation_draft(session_id,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('test','无需创建','{}','[]','preview','2030-01-01T00:00:00+00:00',app.now(),app.now())).lastrowid
            self.assertTrue(app.discard_draft(c,did))
            c.commit()
            self.assertEqual(c.execute('select status from conversation_draft where id=?',(did,)).fetchone()[0],'discarded')
            self.assertEqual(c.execute("select count(*) from audit_log where object_type='conversation_draft' and object_id=? and action='discard'",(did,)).fetchone()[0],1)
        finally: c.close()
    def test_active_approval_delegate_replaces_confirming_user_only(self):
        c=app.db()
        try:
            manager=c.execute("select id from user where role='manager' limit 1").fetchone()[0]
            owner=c.execute("select id from user where role<>'manager' order by id limit 1").fetchone()[0]
            proxy=c.execute("select id from user where id not in (?,?) order by id limit 1",(manager,owner)).fetchone()[0]
            start=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(); end=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
            did=c.execute("insert into delegate(user_id,delegate_user_id,scope,start_at,end_at) values(?,?,?,?,?)",(manager,proxy,'approval',start,end)).lastrowid
            self.assertEqual(app.approver_for(c,owner),proxy)
            c.execute('delete from delegate where id=?',(did,))
            self.assertEqual(app.approver_for(c,owner),manager)
        finally: c.close()
    def test_explicit_task_number_progress_parsing_and_submission(self):
        c=app.db()
        try:
            owner=c.execute("select id from user where role<>'manager' order by id limit 1").fetchone()[0]
            approver=app.approver_for(c,owner)
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,approver_id,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('飞书进度测试',owner,'2030-01-01T00:00:00+00:00','测试记录','approved',approver,app.now(),app.now())).lastrowid
            parsed=app.parse_progress_message(f'任务#{tid} 完成 60%，阻塞：等待接口')
            self.assertEqual(parsed['task_id'],tid); self.assertEqual(parsed['percent'],60); self.assertTrue(parsed['blocked'])
            rid,aid,_=app.submit_progress(c,tid,owner,parsed['percent'],parsed['comment'],parsed['blocked'],parsed['block_reason'],source='feishu')
            self.assertGreater(rid,0); self.assertGreater(aid,0)
            self.assertEqual(c.execute('select source from progress_record where id=?',(rid,)).fetchone()[0],'feishu')
            self.assertEqual(app.parse_progress_message(f'任务#{tid} 已完成')['percent'],100)
        finally: c.close()
    def test_extract_common_document_text_without_inventing_content(self):
        self.assertEqual(app.extract_document_text('会议纪要：完成试点'.encode(),'minutes.txt'),'会议纪要：完成试点')
        import io, zipfile
        body='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>完成评审</w:t></w:r></w:p></w:body></w:document>'.encode()
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as doc: doc.writestr('word/document.xml',body)
        self.assertEqual(app.extract_document_text(stream.getvalue(),'minutes.docx'),'完成评审')
        self.assertEqual(app.extract_document_text(b'not-a-pdf','minutes.pdf'),'')
        self.assertEqual(app.extract_uploaded_text('请完成试点验收'.encode(),'minutes.txt'),'请完成试点验收')
        with self.assertRaisesRegex(ValueError,'仅支持'):
            app.extract_uploaded_text(b'not a supported file','minutes.exe')
    def test_expired_progress_is_auto_confirmed(self):
        c=app.db()
        try:
            tid=c.execute("insert into task(title,owner_id,due_at,deliverable,approval_status,work_status,created_at,updated_at) values(?,?,?,?,?,?,?,?)",('超时进度',2,'2030-01-01T00:00:00+00:00','x','approved','in_progress',app.now(),app.now())).lastrowid
            rid=c.execute("insert into progress_record(task_id,reported_percent,report_text,reported_at) values(?,?,?,?)",(tid,75,'等待确认',app.now())).lastrowid
            aid=app.create_approval(c,'progress_confirm',rid,tid,1); c.execute("update approval_record set created_at='2020-01-01T00:00:00+00:00' where id=?",(aid,)); result=app.run_daily_jobs(c,'2030-01-01','approval')
            self.assertEqual(result['auto_confirmed_progress'],1)
            self.assertEqual(c.execute('select effective_percent from task where id=?',(tid,)).fetchone()[0],75)
        finally: c.close()
    def test_feishu_adapter_is_safe_without_configuration(self):
        for key in ('FEISHU_APP_ID','FEISHU_APP_SECRET','FEISHU_VERIFICATION_TOKEN'): os.environ.pop(key,None)
        self.assertFalse(app.FeishuAdapter().configured())
        self.assertEqual(app.FeishuAdapter().send_text('ou_test','hello')['mode'],'dry-run')
    def test_feishu_profile_cache_can_be_read_without_remote_lookup(self):
        c=app.db()
        try:
            c.execute("insert or replace into feishu_profile(open_id,display_name,avatar_url,fetched_at) values(?,?,?,?)",('ou_profile_test','飞书测试成员','https://example.com/avatar.png',app.now())); c.commit()
            self.assertEqual(app.feishu_profile_for(c,'ou_profile_test'),{'display_name':'飞书测试成员','avatar_url':'https://example.com/avatar.png'})
        finally: c.close()
    def test_feishu_login_mode_requires_server_session(self):
        keys=('WMT_AUTH_MODE','WMT_PUBLIC_BASE_URL','FEISHU_APP_ID','FEISHU_APP_SECRET')
        previous={key:os.environ.get(key) for key in keys}
        os.environ.update({'WMT_AUTH_MODE':'feishu','WMT_PUBLIC_BASE_URL':'https://worktree.example.test','FEISHU_APP_ID':'cli_test','FEISHU_APP_SECRET':'secret'})
        c=app.db(); user=c.execute('select id from user order by id limit 1').fetchone(); session_id='test-feishu-session'; expiry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(); c.execute('insert or replace into login_session(session_id,user_id,expire_at,created_at,last_seen_at) values(?,?,?,?,?)',(session_id,user['id'],expiry,app.now(),app.now())); c.commit(); c.close()
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/api/session',timeout=5) as response: self.assertFalse(json.loads(response.read())['authenticated'])
            with self.assertRaises(HTTPError) as denied: urlopen(base+'/api/users',timeout=5)
            self.assertEqual(denied.exception.code,401)
            request=Request(base+'/api/users',headers={'Cookie':'wmt_session='+session_id})
            with urlopen(request,timeout=5) as response: self.assertEqual(response.status,200)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
            for key,value in previous.items():
                if value is None: os.environ.pop(key,None)
                else: os.environ[key]=value
    def test_wecom_callback_decrypts_and_maps_feedback(self):
        from Crypto.Cipher import AES
        key=base64.b64encode(b'0123456789abcdef').decode().decode() if False else base64.b64encode(b'0123456789abcdef'*2).decode()[:43]
        # 32-byte AES key encoded as the 43-character Enterprise WeChat key.
        os.environ.update({'WECOM_TOKEN':'test-token','WECOM_AES_KEY':key,'WECOM_CORP_ID':'corp-test'})
        codec=app.WeComCallback(); self.assertTrue(codec.configured())
        c=app.db(); c.execute("insert into task(title,owner_id,due_at,deliverable,created_at,updated_at) values(?,?,?,?,?,?)",('callback-task',1,'2030-01-01T00:00:00+00:00','x',app.now(),app.now())); tid=c.execute('select last_insert_rowid()').fetchone()[0]; c.commit()
        xml=f'<xml><FromUserName>wecom-user-1</FromUserName><MsgType>text</MsgType><Content>任务#{tid} 已完成，预计2026-10-20</Content></xml>'.encode()
        raw=b'0123456789abcdef'+struct.pack('>I',len(xml))+xml+b'corp-test'; pad=32-(len(raw)%32); raw+=bytes([pad])*pad
        decoded=base64.b64decode(key+'='); encrypted=base64.b64encode(AES.new(decoded,AES.MODE_CBC,decoded[:16]).encrypt(raw)).decode()
        self.assertEqual(codec.decrypt(encrypted),xml.decode()); self.assertEqual(codec.task_id_from_text(f'任务#{tid} 已完成'),tid); result=codec.handle_message(c,xml.decode()); c.commit(); self.assertEqual(result['task_id'],tid); self.assertEqual(c.execute('select count(*) from feedback where task_id=?',(tid,)).fetchone()[0],1); c.close()

    def test_z_http_api_end_to_end_full_task_lifecycle(self):
        """Exercise the same JSON API calls used by the browser, end to end."""
        old_user=os.environ.pop('WMT_PUBLIC_USER',None); old_password=os.environ.pop('WMT_PUBLIC_PASSWORD',None)
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            def call(path, body=None):
                data=None if body is None else json.dumps(body).encode()
                request=Request(base+path,data=data,headers={'Content-Type':'application/json'} if data else {})
                with urlopen(request,timeout=5) as response:
                    self.assertEqual(response.status,200)
                    return json.loads(response.read())

            def call_status(path, body):
                request=Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                try:
                    with urlopen(request,timeout=5) as response: return response.status
                except HTTPError as error: return error.code

            initial=call('/api/bootstrap')
            self.assertEqual(set(initial),{'dashboard','users','tree','tasks'})
            self.assertIn('active',initial['dashboard'])
            users=initial['users']; tree=initial['tree']
            self.assertGreaterEqual(len(users),2); self.assertTrue(tree)
            manager=next(user for user in users if user['role']=='manager')
            owner=next(user for user in users if user['id']!=manager['id'])
            domain=next(node for node in tree if node['node_type']=='domain')
            child=call('/api/tree',{'parent_id':domain['id'],'node_type':'subdomain','name':'接口测试责任域','boundary':'接口测试边界','owner_id':owner['id']})
            self.assertTrue(any(node['id']==child['id'] for node in call('/api/tree')))

            dept=call('/api/org/departments',{'name':'接口测试部门','owner_user_id':manager['id']})
            position=call('/api/org/positions',{'name':'接口测试岗位','dept_id':dept['id'],'level':'P6'})
            assignment=call('/api/org/user-positions',{'user_id':owner['id'],'position_id':position['id'],'is_primary':True})
            self.assertGreater(assignment['id'],0)
            self.assertTrue(any(row['id']==position['id'] for row in call('/api/org/positions')))

            reporting=call('/api/reporting-lines',{'user_id':owner['id'],'direct_leader_id':manager['id']})
            self.assertGreater(reporting['id'],0)
            reporting_updated=call(f"/api/reporting-lines/{reporting['id']}",{'action':'update','user_id':owner['id'],'direct_leader_id':manager['id']})
            self.assertEqual(reporting_updated['status'],'updated')
            self.assertEqual(call(f"/api/reporting-lines/{reporting_updated['id']}",{'action':'delete'})['status'],'deleted')
            reporting=call('/api/reporting-lines',{'user_id':owner['id'],'direct_leader_id':manager['id']})
            self.assertGreater(reporting['id'],0)
            member_line=call(f"/api/users/{owner['id']}/reporting-line",{'direct_leader_id':manager['id']})
            self.assertEqual(member_line['status'],'updated')
            self.assertEqual(call(f"/api/users/{owner['id']}/reporting-line",{'direct_leader_id':None})['status'],'cleared')
            reporting=call('/api/reporting-lines',{'user_id':owner['id'],'direct_leader_id':manager['id']})
            self.assertGreater(reporting['id'],0)
            delegate=call('/api/delegates',{'user_id':manager['id'],'delegate_user_id':owner['id'],'scope':'approval','start_at':'2000-01-01T00:00:00+00:00','end_at':'2000-01-02T00:00:00+00:00'})
            self.assertGreater(delegate['id'],0)
            self.assertTrue(any(row['id']==delegate['id'] for row in call('/api/delegates')))

            template=call('/api/routine/templates',{'org_node_id':child['id'],'title':'接口测试例行任务','owner_id':owner['id'],'deliverable':'测试记录','recurrence':'DAILY'})
            self.assertGreater(template['id'],0)
            generated=call('/api/routine/generate',{'instance_date':'2031-01-01'})
            self.assertGreaterEqual(generated['count'],1)
            self.assertEqual(call('/api/routine/generate',{'instance_date':'2031-01-01'})['count'],0)

            manual=call('/api/tasks',{'org_node_id':child['id'],'title':'接口测试手工任务','owner_id':owner['id'],'due_at':'2031-01-02T17:00:00+08:00','deliverable':'手工任务验收记录'})
            self.assertEqual(manual['approval_status'],'pending')
            from urllib.parse import quote
            self.assertTrue(any(row['id']==manual['id'] for row in call('/api/tasks/similar?title='+quote('接口测试手工任务'))))
            self.assertFalse(any(row['id']==manual['id'] for row in call('/api/tasks')))
            self.assertFalse(any(row['id']==manual['id'] for row in call('/api/bootstrap')['tasks']))
            self.assertEqual(call(f"/api/approvals/{manual['approval_id']}/confirm",{'result':'approved','operator_id':manager['id']})['status'],'approved')
            self.assertEqual(call_status(f"/api/tasks/{manual['id']}",{'work_status':'in_progress','operator_id':manager['id']}),403)
            self.assertEqual(call(f"/api/tasks/{manual['id']}",{'work_status':'in_progress','operator_id':owner['id']})['status'],'ok')
            self.assertEqual(call(f"/api/tasks/{manual['id']}",{'work_status':'awaiting_acceptance','operator_id':owner['id']})['status'],'ok')
            self.assertEqual(call_status(f"/api/tasks/{manual['id']}/accept",{'result':'passed','acceptor_id':owner['id']}),403)
            self.assertEqual(call(f"/api/tasks/{manual['id']}/accept",{'result':'passed','acceptor_id':manager['id']})['status'],'ok')
            manual_done=next(row for row in call('/api/tasks') if row['id']==manual['id'])
            self.assertEqual(manual_done['work_status'],'completed')
            self.assertEqual(call(f"/api/tasks/{manual['id']}",{'action':'delete','operator_id':owner['id']})['status'],'ok')
            self.assertFalse(any(row['id']==manual['id'] for row in call('/api/tasks')))

            draft=call('/api/task/draft/parse',{'text':'完成接口端到端验收','user_id':owner['id'],'session_id':'e2e'})
            self.assertIn('due_at',draft['missing_slots'])
            completed=call(f"/api/task/draft/{draft['id']}/complete",{'due_at':'2031-01-02T17:00:00+08:00','deliverable':'接口验收记录','org_node_id':domain['id']})
            self.assertEqual(completed['missing_slots'],[])
            created=call(f"/api/task/draft/{draft['id']}/confirm",{})
            self.assertEqual(created['status'],'pending')
            self.assertTrue(any(item['id']==created['approval_id'] for item in call('/api/approvals')))
            approved=call(f"/api/approvals/{created['approval_id']}/confirm",{'result':'approved','operator_id':manager['id']})
            self.assertEqual(approved['status'],'approved')

            progress=call(f"/api/tasks/{created['task_id']}/progress",{'reporter_id':owner['id'],'percent':100,'comment':'接口端到端验收完成'})
            self.assertEqual(progress['status'],'pending')
            self.assertEqual(call(f"/api/approvals/{progress['approval_id']}/confirm",{'result':'approved','operator_id':manager['id']})['status'],'approved')
            task=next(row for row in call('/api/tasks') if row['id']==created['task_id'])
            self.assertEqual(task['work_status'],'completed')
            self.assertEqual(task['effective_percent'],100)
            self.assertTrue(call(f"/api/progress?task_id={created['task_id']}"))

            self.assertEqual(call(f"/api/tasks/{created['task_id']}/feedback",{'content':'验收通过','user_id':owner['id']})['status'],'ok')
            self.assertEqual(call(f"/api/tasks/{created['task_id']}",{'priority':'P0'})['status'],'ok')
            discarded=call('/api/task/draft/parse',{'text':'无需立项的测试信息','session_id':'discard-e2e'})
            self.assertEqual(call(f"/api/task/draft/{discarded['id']}/discard",{})['status'],'discarded')

            new_user=call('/api/users',{'name':'接口测试成员','wecom_userid':'api-e2e-user','role':'member'})
            bound=call(f"/api/users/{new_user['id']}/feishu-open-id",{'feishu_open_id':'ou_api_e2e_test'})
            self.assertEqual(bound['status'],'bound')
            bindings=call('/api/feishu/bindings')
            self.assertTrue(any(x['user_id']==new_user['id'] and x['open_id_masked'].endswith('test') for x in bindings))
            history=call('/api/feishu/binding-history')
            self.assertTrue(any(x['user_id']==new_user['id'] and x['action']=='bind' for x in history))
            self.assertNotIn('ou_api_e2e_test',json.dumps(history,ensure_ascii=False))
            self.assertEqual(call(f"/api/users/{new_user['id']}/feishu-open-id",{'feishu_open_id':'ou_api_e2e_next'})['status'],'modified')
            self.assertEqual(call(f"/api/users/{new_user['id']}/feishu-open-id/unbind",{})['status'],'unbound')
            history=call('/api/feishu/binding-history')
            self.assertTrue(any(x['user_id']==new_user['id'] and x['action']=='unbind' for x in history))
            self.assertIn('requires_human_confirmation',call('/api/ai/draft',{'text':'请在2031年1月3日前完成接口测试'}))
            self.assertIn('total',call('/api/dashboard'))
            self.assertIn('targets',call('/api/ops/daily-preview?date=2031-01-01'))
            self.assertIn('morning',call('/api/ops/daily-run',{'report_date':'2031-01-01','kind':'all'}))
            self.assertIsInstance(call('/api/feishu/unbound-identities'),list)
            self.assertEqual(call('/api/notify',{'userid':'api-e2e-user','text':'test'})['mode'],'dry-run')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
            if old_user is not None: os.environ['WMT_PUBLIC_USER']=old_user
            if old_password is not None: os.environ['WMT_PUBLIC_PASSWORD']=old_password

if __name__=='__main__': unittest.main()

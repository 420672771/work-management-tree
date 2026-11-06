"""Local accounts, password hashing and database-backed login throttling."""
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_account (
 user_id INTEGER PRIMARY KEY REFERENCES user(id) ON DELETE CASCADE,
 username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL,
 password_changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_login_attempt (
 rate_key TEXT PRIMARY KEY, username TEXT NOT NULL, attempts INTEGER NOT NULL,
 window_start TEXT NOT NULL
);
"""
ITERATIONS = 600000
DEFAULT_INITIAL_PASSWORD = '123456'


class Conflict(ValueError):
    pass


class Throttled(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def username(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{2,63}',value.strip()):
        raise ValueError('账号须为 3—64 位字母、数字或 _.@-，并以字母或数字开头')
    return value.strip().lower()


def check_password(value):
    if not isinstance(value,str) or not 6<=len(value)<=128:
        raise ValueError('密码长度须为 6—128 个字符')
    try:value.encode('utf-8')
    except UnicodeError:raise ValueError('密码包含无效字符')
    return value


def hash_password(value):
    value=check_password(value);salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',value.encode(),salt.encode(),ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def verify_password(value,encoded):
    if not isinstance(value,str) or len(value)>128:return False
    try:
        scheme,rounds,salt,expected=encoded.split('$')
        if scheme!='pbkdf2_sha256' or not 100000<=int(rounds)<=2000000:return False
        actual=hashlib.pbkdf2_hmac('sha256',value.encode(),salt.encode(),int(rounds)).hex()
        return hmac.compare_digest(actual,expected)
    except (AttributeError,ValueError,TypeError,UnicodeError):return False


def metadata(c,user_id):
    row=c.execute('SELECT username,password_changed_at FROM user_account WHERE user_id=?',(user_id,)).fetchone()
    return dict(row) if row else {'username':'','password_changed_at':None}


def public_user(c,value):
    value=dict(value);value.update(metadata(c,value['id']));return value


def audit(c,user_id,action,actor,details=None):
    import json
    c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?,?)',
              ('user_account',user_id,action,str(actor),json.dumps(details or {},ensure_ascii=False),now()))


def configure(c,user_id,login_name,password,actor):
    login_name=username(login_name)
    if not c.execute('SELECT 1 FROM user WHERE id=?',(user_id,)).fetchone():raise LookupError('员工不存在')
    previous=c.execute('SELECT username,password_hash FROM user_account WHERE user_id=?',(user_id,)).fetchone()
    if password is None:password=''
    if not isinstance(password,str):raise ValueError('密码格式无效')
    if previous is None and password=='':password=DEFAULT_INITIAL_PASSWORD
    encoded=hash_password(password) if password else previous['password_hash']
    stamp=now()
    try:
        c.execute('''INSERT INTO user_account(user_id,username,password_hash,password_changed_at) VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,
            password_changed_at=CASE WHEN ? THEN excluded.password_changed_at ELSE user_account.password_changed_at END''',
            (user_id,login_name,encoded,stamp,bool(password)))
    except sqlite3.IntegrityError:raise Conflict('账号已被其他员工使用')
    if password or (previous and previous['username']!=login_name):
        c.execute('DELETE FROM login_session WHERE user_id=?',(user_id,))
    audit(c,user_id,'configure',actor,{'username':login_name,'password_set':bool(password)})
    return metadata(c,user_id)


def reset_password(c,user_id,password,actor):
    account=c.execute('SELECT username FROM user_account WHERE user_id=?',(user_id,)).fetchone()
    if not account:raise ValueError('该员工尚未配置登录账号，请先在编辑成员中设置账号和初始密码')
    encoded=hash_password(password)
    c.execute('UPDATE user_account SET password_hash=?,password_changed_at=? WHERE user_id=?',(encoded,now(),user_id))
    c.execute('DELETE FROM login_session WHERE user_id=?',(user_id,))
    c.execute('DELETE FROM account_login_attempt WHERE username=?',(account['username'],))
    audit(c,user_id,'reset_password',actor)


def change_password(c,user_id,old_password,new_password):
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
    old=c.execute('SELECT password_hash FROM user_account WHERE user_id=?',(user_id,)).fetchone()
    if not old:raise ValueError('尚未配置账号密码，请联系负责人或管理 PM 设置')
    if not verify_password(old_password,old['password_hash']):raise PermissionError('当前密码不正确')
    if old_password==new_password:raise ValueError('新密码不能与当前密码相同')
    c.execute('UPDATE user_account SET password_hash=?,password_changed_at=? WHERE user_id=?',(hash_password(new_password),now(),user_id))
    c.execute('DELETE FROM login_session WHERE user_id=?',(user_id,))
    audit(c,user_id,'change_password',user_id)


def authenticate(c,login_name,password,client_address):
    if not isinstance(login_name,str) or len(login_name)>64 or not isinstance(password,str) or len(password)>128:
        raise PermissionError('账号或密码不正确')
    try:login_name=username(login_name)
    except ValueError:raise PermissionError('账号或密码不正确')
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
    stamp=datetime.now(timezone.utc)
    key=hashlib.sha256((str(client_address)+'\0'+login_name).encode()).hexdigest()
    c.execute('DELETE FROM account_login_attempt WHERE window_start<?',((stamp-timedelta(days=1)).isoformat(),))
    rate=c.execute('SELECT attempts,window_start FROM account_login_attempt WHERE rate_key=?',(key,)).fetchone()
    if rate and datetime.fromisoformat(rate['window_start'])>stamp-timedelta(minutes=15) and rate['attempts']>=5:
        c.commit();raise Throttled('登录尝试过多，请 15 分钟后重试或联系管理员重置密码')
    row=c.execute('SELECT * FROM user_account WHERE username=?',(login_name,)).fetchone()
    dummy=f'pbkdf2_sha256${ITERATIONS}$dummy-salt-for-missing-user$'+('0'*64)
    okay=verify_password(password,row['password_hash'] if row else dummy)
    if not row or not okay:
        active=rate and datetime.fromisoformat(rate['window_start'])>stamp-timedelta(minutes=15)
        attempts=rate['attempts']+1 if active else 1;window=rate['window_start'] if active else stamp.isoformat()
        c.execute('INSERT OR REPLACE INTO account_login_attempt VALUES(?,?,?,?)',(key,login_name,attempts,window));c.commit()
        raise PermissionError('账号或密码不正确')
    c.execute('DELETE FROM account_login_attempt WHERE rate_key=?',(key,))
    audit(c,row['user_id'],'password_login',row['user_id'])
    return row['user_id']


def new_session(c,user_id):
    token=secrets.token_urlsafe(48);stamp=now()
    c.execute('DELETE FROM login_session WHERE expire_at<=?',(stamp,))
    c.execute('INSERT INTO login_session VALUES(?,?,?,?,?)',(token,user_id,(datetime.now(timezone.utc)+timedelta(hours=8)).isoformat(),stamp,stamp))
    return token

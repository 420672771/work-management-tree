#!/usr/bin/env python3
"""Encrypt production reference data for Git delivery; decrypt/install offline."""
import argparse
import base64
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from urllib.parse import quote

from Crypto.Cipher import AES

MAGIC=b'WMTBASE1'
FILES={'production-base.db','production-base.db.report.json','README.txt','SHA256SUMS'}
TABLES={'user','org_department','org_position','user_position','reporting_line','delegate',
        'org_node','task_template','user_account','feishu_profile','performance_config','system_setting'}


def write_new(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream:stream.write(data)


def unpack(payload):
    if len(payload)>32*1024*1024:raise ValueError('迁移包过大')
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as tar:
        members=tar.getmembers()
        if {item.name for item in members}!=FILES or len(members)!=len(FILES):raise ValueError('迁移包文件列表不正确')
        if any(not item.isfile() or item.size>32*1024*1024 for item in members):raise ValueError('迁移包包含不支持的文件')
        files={item.name:tar.extractfile(item).read() for item in members}
    expected={}
    for line in files['SHA256SUMS'].decode().splitlines():
        digest,name=line.split('  ',1)
        if name in expected or name not in FILES-{'SHA256SUMS'}:raise ValueError('迁移包校验列表不正确')
        expected[name]=digest
    if set(expected)!=FILES-{'SHA256SUMS'}:raise ValueError('迁移包缺少校验项')
    for name,digest in expected.items():
        if hashlib.sha256(files[name]).hexdigest()!=digest:raise ValueError('迁移包文件校验失败：'+name)
    report=json.loads(files['production-base.db.report.json'])
    counts=report.get('included_counts',{})
    if report.get('scope')!='base' or set(counts)!=TABLES:raise ValueError('仅支持完整的正式基础资料包')
    with tempfile.TemporaryDirectory(prefix='wmt-package-verify-') as folder:
        database=Path(folder)/'verify.db';write_new(database,files['production-base.db'])
        with sqlite3.connect('file:'+quote(str(database))+'?mode=ro',uri=True) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('数据库完整性检查失败')
            if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('数据库外键检查失败')
            for table,count in counts.items():
                if type(count) is not int or count<0 or c.execute('SELECT count(*) FROM '+table).fetchone()[0]!=count:raise ValueError('数据库数量与导出报告不一致：'+table)
            for table in ('task','task_instance','weekly_report','monthly_report','login_session','bot_pending_action','notify_log'):
                if c.execute('SELECT count(*) FROM '+table).fetchone()[0]:raise ValueError('基础资料包不应包含业务或会话记录')
    return files['production-base.db'],report


def seal(source,output,key_output):
    source=Path(source).resolve();output=Path(output).resolve();key_output=Path(key_output).resolve()
    if len({source,output,key_output})!=3:raise ValueError('输入、输出和密钥文件必须不同')
    if output.exists() or key_output.exists():raise ValueError('目标或密钥文件已存在，拒绝覆盖')
    payload=source.read_bytes();_,report=unpack(payload)
    key=os.urandom(32);nonce=os.urandom(12)
    cipher=AES.new(key,AES.MODE_GCM,nonce=nonce);cipher.update(MAGIC)
    ciphertext,tag=cipher.encrypt_and_digest(payload)
    write_new(key_output,base64.urlsafe_b64encode(key).rstrip(b'=')+b'\n')
    try:write_new(output,MAGIC+nonce+tag+ciphertext)
    except Exception:
        key_output.unlink();raise
    return report


def decrypt(package,key_file=None):
    encoded=Path(key_file).read_text().strip() if key_file else getpass.getpass('迁移包解密密钥：').strip()
    try:key=base64.b64decode(encoded+'='*((-len(encoded))%4),altchars=b'-_',validate=True)
    except (ValueError,UnicodeError):raise ValueError('密钥格式无效') from None
    if len(key)!=32:raise ValueError('密钥格式无效')
    data=Path(package).read_bytes()
    if len(data)<36 or data[:8]!=MAGIC:raise ValueError('不支持的加密包格式')
    cipher=AES.new(key,AES.MODE_GCM,nonce=data[8:20]);cipher.update(MAGIC)
    try:payload=cipher.decrypt_and_verify(data[36:],data[20:36])
    except ValueError:raise ValueError('密钥不正确或加密包已损坏，未安装数据') from None
    return unpack(payload)


def install(package,database,key_file=None):
    database=Path(database).resolve()
    if database.exists() or any(Path(str(database)+suffix).exists() for suffix in ('-wal','-shm')):
        raise ValueError('目标数据库或 WAL/SHM 已存在，拒绝覆盖；请先安装到新的候选文件并核对生产数据')
    content,report=decrypt(package,key_file)
    database.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    handle,name=tempfile.mkstemp(prefix='.production-install-',suffix='.db',dir=database.parent)
    temporary=Path(name)
    try:
        with os.fdopen(handle,'wb') as stream:stream.write(content)
        os.link(temporary,database)
    finally:temporary.unlink(missing_ok=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    pack=commands.add_parser('seal');pack.add_argument('--source',type=Path,required=True);pack.add_argument('--output',type=Path,required=True);pack.add_argument('--key-output',type=Path,required=True)
    for name in ('inspect','install'):
        command=commands.add_parser(name);command.add_argument('--package',type=Path,required=True);command.add_argument('--key-file',type=Path)
        if name=='install':command.add_argument('--database',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='seal':report=seal(args.source,args.output,args.key_output)
        elif args.command=='install':report=install(args.package,args.database,args.key_file)
        else:_,report=decrypt(args.package,args.key_file)
    except (OSError,ValueError,sqlite3.Error,tarfile.TarError) as exc:raise SystemExit('操作失败：'+str(exc))
    print(json.dumps({'status':'ok','scope':report['scope'],'generated_at':report['generated_at'],'included_counts':report['included_counts']},ensure_ascii=False,indent=2))
    if args.command=='install':print('已安装候选数据库：'+str(args.database.resolve())+'；尚未切换运行中的服务。')


if __name__=='__main__':main()

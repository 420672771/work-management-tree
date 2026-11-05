#!/usr/bin/env python3
"""Server-console bootstrap/recovery; password is never passed in command arguments."""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import accounts

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='为已有员工配置登录账号；通过终端隐藏输入密码')
    parser.add_argument('--user-id',type=int,required=True)
    parser.add_argument('--username',required=True)
    args=parser.parse_args()
    password=getpass.getpass('初始/重置密码：')
    if password!=getpass.getpass('再次输入密码：'):raise SystemExit('两次密码不一致，未修改')
    app.initialize();c=app.db()
    try:
        accounts.configure(c,args.user_id,args.username,password,'server-console');c.commit()
        print('账号已配置，原登录会话已失效。')
    finally:c.close()

"""Run with PYTHONPATH=backend (and local dependency directory, when needed)."""
import argparse,getpass,secrets,uuid
from app.db import database,indexes
from app.services.demo import seed
from app.services.auth import hash_password

p=argparse.ArgumentParser(); p.add_argument('command',choices=['seed','admin']); p.add_argument('--mode',default='demo',choices=['demo','imported','live']); p.add_argument('--username',default='admin'); p.add_argument('--reset',action='store_true'); args=p.parse_args()
db=database(args.mode); indexes(db)
if args.command=='seed': print(seed(db,args.reset))
else:
    password=getpass.getpass('New admin password (12+ characters): ')
    db.users.update_one({'username':args.username},{'$set':{'password':hash_password(password),'role':'admin'},'$setOnInsert':{'id':uuid.uuid4().hex,'username':args.username}},upsert=True)
    print('Administrator configured for '+args.mode)

import hashlib,hmac,secrets,os
from datetime import datetime,timedelta,timezone
from fastapi import HTTPException,Request
from app.db import database

def hash_password(password,salt=None):
    if len(password)<12: raise ValueError('Use at least 12 characters')
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()

def verify(password,encoded):
    try: return hmac.compare_digest(hash_password(password,encoded.split(':')[0]),encoded)
    except ValueError: return False

def issue(db,user):
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(24)
    db.sessions.insert_one(dict(token=hashlib.sha256(token.encode()).hexdigest(),user_id=user['id'],csrf=csrf,expires=datetime.now(timezone.utc)+timedelta(days=1)))
    return token,csrf

def current(request:Request,mode,role=None):
    db=database(mode); token=request.cookies.get('ml_session','')
    session=db.sessions.find_one({'token':hashlib.sha256(token.encode()).hexdigest(),'expires':{'$gt':datetime.now(timezone.utc)}})
    user=db.users.find_one({'id':session['user_id']}) if session else None
    if not user: raise HTTPException(401,'Sign in to continue')
    if request.method not in ('GET','HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']): raise HTTPException(403,'CSRF token required')
    if role and user['role'] not in role: raise HTTPException(403,'Role does not permit this action')
    return user

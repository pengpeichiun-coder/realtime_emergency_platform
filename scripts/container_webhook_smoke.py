"""Real signed HTTP delivery to an explicitly allowed worker-local receiver."""
import json
import os
import secrets
import subprocess
import sys
import time
from uuid import uuid4
import urllib.request

base=os.environ.get('BASE_URL','http://127.0.0.1:8080')
token=None

def request(method,path,data=None):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    body=json.dumps(data).encode() if data is not None else None
    with urllib.request.urlopen(urllib.request.Request(base+path,data=body,headers=headers,method=method),timeout=10) as response:
        return json.load(response) if response.status!=204 else None

registered=request('POST','/api/auth/register',{'organization_name':'Webhook smoke '+uuid4().hex,'display_name':'Smoke admin','email':uuid4().hex+'@example.com','password':secrets.token_urlsafe(24)})
token=registered['access_token']
subscription=request('POST','/api/webhooks',{'name':'Worker-local receiver','url':'http://127.0.0.1:8765/events','event_types':['incident.created']})
publisher_outage='--publisher-outage' in sys.argv
retry_receiver='--retry-receiver' in sys.argv
expected_attempts=2 if retry_receiver else 1
code='''import hmac, hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
secret=SECRET
retry=RETRY
attempts=ATTEMPTS
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  body=self.rfile.read(int(self.headers['Content-Length']))
  expected='sha256='+hmac.new(secret.encode(),self.headers['X-Emergency-Timestamp'].encode()+b'.'+body,hashlib.sha256).hexdigest()
  valid=hmac.compare_digest(expected,self.headers['X-Emergency-Signature'])
  self.server.count+=1
  status=200 if valid and (not retry or self.server.count>1) else (500 if valid else 400)
  self.send_response(status);self.end_headers()
  print('ATTEMPT_'+str(self.server.count)+':'+str(status),flush=True)
server=HTTPServer(('127.0.0.1',8765),Handler)
server.timeout=30
server.count=0
print('READY',flush=True)
for _ in range(attempts):
 before=server.count
 server.handle_request()
 if server.count==before:raise RuntimeError('receiver timed out')
server.server_close()
'''.replace('SECRET',repr(subscription['signing_secret'])).replace('RETRY',repr(retry_receiver)).replace('ATTEMPTS',str(expected_attempts))
process=subprocess.Popen(['docker','compose','exec','-T','worker','python','-u','-'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    process.stdin.write(code);process.stdin.close()
    assert process.stdout.readline().strip()=='READY'
    if publisher_outage:
        subprocess.run(['docker','compose','stop','publisher'],check=True,capture_output=True)
    organization=request('GET','/api/auth/me')['organization_id']
    request('POST','/api/incidents',{'type':'medical','severity':'low','description':'Fictional container webhook','latitude':1.3,'longitude':103.85})
    if publisher_outage:
        pending=subprocess.check_output(['docker','compose','exec','-T','postgres','psql','-U','emergency','-d','emergency_platform','-Atc',
            "SELECT count(*) FROM outbox_events WHERE organization_id='"+organization+"' AND processed_at IS NULL"],text=True).strip()
        assert int(pending)>0
        subprocess.run(['docker','compose','start','publisher'],check=True,capture_output=True)
    attempts=[process.stdout.readline().strip() for _ in range(expected_attempts)]
    expected=[f'ATTEMPT_{number}:{"500" if retry_receiver and number==1 else "200"}' for number in range(1,expected_attempts+1)]
    assert attempts==expected,attempts
    process.wait(timeout=10);assert process.returncode==0
    for _ in range(100):
        history=request('GET','/api/webhook-deliveries')
        history=[row for row in history if row['subscription_id']==subscription['id']]
        if history and history[0]['status']=='DELIVERED' and history[0]['attempt_count']==expected_attempts:break
        time.sleep(.2)
    else:raise AssertionError('Delivery did not complete')
    print(f'PASS real container outbox and signed HTTP delivery; {expected_attempts} verified attempt(s), final history DELIVERED.')
finally:
    if publisher_outage:
        subprocess.run(['docker','compose','start','publisher'],check=False,capture_output=True)
    request('DELETE','/api/webhooks/'+subscription['id'])
    if process.poll() is None:process.terminate();process.wait(timeout=5)

/// <reference types="node" />
import assert from 'node:assert/strict'
import test from 'node:test'
import { OperationsStream } from './connection.ts'
import type { OperationsSnapshot } from '../types/domain.ts'

const time = '2026-09-22T04:00:00Z'
function snapshot(sequence = 0, streamId = 'stream-a'): OperationsSnapshot {
  return {streamId,sequence,incidents:[],units:[],dispatches:[],activity:[]}
}
function ready(stream_id = 'stream-a') { return {type:'connection.ready',timestamp:time,stream_id,sequence:0,data:{}} }
function activity(sequence: number) {return {type:'activity.created',timestamp:time,stream_id:'stream-a',sequence,data:{id:`ACT-${sequence}`,event_type:'incident.created',entity_type:'incident',entity_id:'INC-1',message:'Reported',timestamp:time,metadata:{}}}}
class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((event: {data:string}) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  closes = 0
  message(data: unknown) {this.onmessage?.({data:JSON.stringify(data)})}
  close() {this.closes++;this.onclose?.()}
}
async function until(check:()=>boolean) {
  for(let i=0;i<100;i++) {if(check()) return; await new Promise(resolve=>setTimeout(resolve,5))}
  assert.fail('Condition did not become true')
}
function deferred<T>() {
  let resolve!: (value:T)=>void
  const promise = new Promise<T>(done=>{resolve=done})
  return {promise,resolve}
}
function setup(fetchSnapshot:(signal:AbortSignal)=>Promise<OperationsSnapshot>) {
  const sockets:FakeSocket[]=[]
  const updates:OperationsSnapshot[]=[]
  const errors:(string|null)[]=[]
  const statuses:string[]=[]
  const resets:string[]=[]
  const stream=new OperationsStream({url:'ws://test/ws/events',fetchSnapshot,
    createSocket:()=>{const socket=new FakeSocket();sockets.push(socket);return socket as unknown as WebSocket},
    onSnapshot:value=>updates.push(value),onStatus:value=>statuses.push(value),onError:value=>errors.push(value),onReset:()=>resets.push('reset'),retryBaseMs:5,retryMaxMs:20})
  return {stream,sockets,updates,errors,statuses,resets}
}

test('connect waits for handshake, buffers events across REST snapshot and applies only newer sequence',async()=>{
 const fetch=deferred<OperationsSnapshot>()
 let calls=0
 const ctx=setup(async()=>{calls++;return fetch.promise})
 try {ctx.stream.start();assert.equal(calls,0);ctx.sockets[0].message(ready());ctx.sockets[0].message(activity(1));fetch.resolve(snapshot());await until(()=>ctx.updates.length===1);assert.equal(ctx.updates[0].sequence,1);assert.equal(ctx.updates[0].activity[0].id,'ACT-1');assert.equal(ctx.statuses.at(-1),'live')} finally {ctx.stream.stop()}
})

test('missing events trigger REST resynchronization instead of inventing state',async()=>{
 let calls=0
 const ctx=setup(async()=>snapshot(calls++===0?0:3))
 try {ctx.stream.start();ctx.sockets[0].message(ready());await until(()=>ctx.updates.length===1);ctx.sockets[0].message(activity(3));await until(()=>calls===2&&ctx.updates.at(-1)?.sequence===3);assert.equal(ctx.updates.at(-1)?.activity.length,0)} finally {ctx.stream.stop()}
})

test('disconnect reconnects with a new REST snapshot and accepts a restarted server stream',async()=>{
 let calls=0
 const ctx=setup(async()=>snapshot(0,calls++===0?'stream-a':'stream-b'))
 try {ctx.stream.start();ctx.sockets[0].message(ready());await until(()=>ctx.updates.length===1);ctx.sockets[0].close();await until(()=>ctx.sockets.length===2);ctx.sockets[1].message(ready('stream-b'));await until(()=>ctx.updates.length===2);assert.equal(ctx.updates[1].streamId,'stream-b');assert.equal(calls,2);assert(ctx.statuses.includes('reconnecting'))} finally {ctx.stream.stop()}
})

test('stop aborts an in-flight snapshot and prevents reconnect or late state writes',async()=>{
 const fetch=deferred<OperationsSnapshot>()
 let signal:AbortSignal|undefined
 const ctx=setup(async input=>{signal=input;return fetch.promise})
 ctx.stream.start();ctx.sockets[0].message(ready());ctx.stream.stop();assert.equal(signal?.aborted,true);fetch.resolve(snapshot());await new Promise(resolve=>setTimeout(resolve,25));assert.equal(ctx.updates.length,0);assert.equal(ctx.sockets.length,1);assert.equal(ctx.sockets[0].closes,1)
})

test('overlapping resync discards an older HTTP response even if it ignores cancellation',async()=>{
 const first=deferred<OperationsSnapshot>()
 const second=deferred<OperationsSnapshot>()
 let calls=0
 const ctx=setup(async()=>++calls===1?first.promise:second.promise)
 try {ctx.stream.start();ctx.sockets[0].message(ready());const refresh=ctx.stream.refresh();second.resolve(snapshot(4));await refresh;first.resolve(snapshot(1));await new Promise(resolve=>setTimeout(resolve,5));assert.deepEqual(ctx.updates.map(item=>item.sequence),[4])} finally {ctx.stream.stop()}
})

test('malformed messages leave current state intact and reconnect with an error',async()=>{
 const ctx=setup(async()=>snapshot())
 try {ctx.stream.start();ctx.sockets[0].message(ready());await until(()=>ctx.updates.length===1);ctx.sockets[0].onmessage?.({data:'{bad json'});await until(()=>ctx.sockets.length===2);assert.equal(ctx.updates.length,1);assert(ctx.errors.some(Boolean));assert.equal(ctx.sockets[0].closes,1)} finally {ctx.stream.stop()}
})

test('failed REST synchronization retries with bounded backoff and stop cancels retry',async()=>{
 let calls=0
 const ctx=setup(async()=>{calls++;throw new Error('API unavailable')})
 ctx.stream.start();ctx.sockets[0].message(ready());await until(()=>ctx.sockets.length===2);ctx.stream.stop();await new Promise(resolve=>setTimeout(resolve,30));assert.equal(calls,1);assert.equal(ctx.sockets.length,2);assert(ctx.errors.includes('API unavailable'))
})

test('socket authenticates in its first frame and never places the token in its URL', async () => {
  const socket=new FakeSocket() as FakeSocket & {send:(data:string)=>void}
  const sent:string[]=[];socket.send=data=>sent.push(data)
  let requested=''
  const stream=new OperationsStream({url:'ws://test/ws/events',authenticate:async()=>'access-credential',
    fetchSnapshot:async()=>snapshot(),createSocket:url=>{requested=url;return socket as unknown as WebSocket},
    onSnapshot:()=>{},onStatus:()=>{},onError:()=>{},onReset:()=>{}})
  try {
    stream.start();socket.onopen?.();await until(()=>sent.length===1)
    assert.equal(requested,'ws://test/ws/events')
    assert.deepEqual(JSON.parse(sent[0]),{type:'authenticate',access_token:'access-credential'})
  } finally {stream.stop()}
})

test('stopped sockets cannot refresh credentials on a late open event', async () => {
  const socket=new FakeSocket();let authentications=0
  const stream=new OperationsStream({url:'ws://test/ws/events',authenticate:async()=>{authentications++;return 'token'},
    fetchSnapshot:async()=>snapshot(),createSocket:()=>socket as unknown as WebSocket,
    onSnapshot:()=>{},onStatus:()=>{},onError:()=>{},onReset:()=>{}})
  stream.start();stream.stop();socket.onopen?.()
  await new Promise(resolve=>setTimeout(resolve,0));assert.equal(authentications,0)
})

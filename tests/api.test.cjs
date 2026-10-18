const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/api.js'),'utf8');
const settle=()=>new Promise(r=>setImmediate(r));
function setup(fetch){const s={fetch,setTimeout,clearTimeout,AbortController,console:{error(){}},alert(){}};s.window=s;vm.createContext(s);vm.runInContext(source,s);return s;}
test('RPC uses same-origin JSON POST and retains per-call callbacks',async()=>{
 const requests=[],responses=[];
 const s=setup(async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({ok:true})};});
 s.google.script.run.withSuccessHandler(()=>responses.push('one')).dataPanitia();
 s.google.script.run.withSuccessHandler(()=>responses.push('two')).checkinTamu('TEST','Pos 1');
 await settle();assert.deepEqual(responses,['one','two']);
 assert.equal(requests[0].url,'/api/rpc');assert.equal(requests[0].options.method,'POST');
 assert.equal(requests[0].options.credentials,'same-origin');
 assert.deepEqual(JSON.parse(requests[1].options.body).args,['TEST','Pos 1']);
});
test('network error does not replay a write',async()=>{
 let calls=0,error;const s=setup(async()=>{calls++;throw new Error('Offline');});
 s.google.script.run.withFailureHandler(e=>error=e.message).simpanKonfirmasi({});
 await settle();assert.equal(calls,1);assert.equal(error,'Offline');
});
test('backend errors reach failure handler instead of success UI',async()=>{
 let error,success=false;const s=setup(async()=>({ok:false,json:async()=>({error:'Silakan masuk'})}));
 s.google.script.run.withSuccessHandler(()=>success=true).withFailureHandler(e=>error=e.message).dataPanitia();
 await settle();assert.equal(success,false);assert.equal(error,'Silakan masuk');
});

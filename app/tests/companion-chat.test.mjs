import {test} from 'node:test';
import assert from 'node:assert/strict';
import {CompanionChat,taskView,taskTool,buildContext,LIMITS} from '../dsh_plugin/companion-chat.mjs';
import {createBridge} from '../dsh_plugin/index.mjs';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const personaFile=fileURLToPath(new URL('../dsh_plugin/persona.md',import.meta.url));
const e=(seq,type,data={})=>({seq,type,data,timestamp:'2026-09-30T12:00:00Z'});
const fixture={meta:{cwd:'Y:/project',system:'SECRET-HARNESS'},events:[
  e(0,'user/message',{source:{kind:'skill-catalog'},content:[{type:'text',text:'PRIVATE-CATALOG'}]}),
  e(1,'turn/start'),e(2,'user/message',{source:{kind:'user'},content:[{type:'text',text:'修复显示问题'}]}),
  e(3,'tool/call',{callId:'a',call:{name:'read',arguments:'SECRET-TOOL-ARGS'}}),
  e(4,'tool/result',{message:{toolCallId:'a',content:[{type:'text',text:'SECRET-TOOL-RESULT'}]}}),
  e(5,'assistant/message',{content:[{type:'reasoning',text:'PRIVATE-THOUGHT'},{type:'text',text:'修改完成，检查通过。'}]}),
  e(6,'turn/end',{reason:{kind:'completed'}})]};
function fakeLlm(responses,{efforts=['off','high']}={}) {
  let next=0; const requests=[];
  return {requests,async resolveModelInfo(){return {reasoning:{efforts:efforts.map(id=>({id}))}};},
    async *stream(request) {
      requests.push(structuredClone({...request,signal:undefined}));
      const content=responses[next++]||[];
      for (let index=0;index<content.length;index++) yield {type:'block-end',index,block:content[index]};
      yield {type:'finish',reason:{kind:content.some(b=>b.type==='tool-call')?'tool-calls':'stop'}};
    }};
}
const answer=(action='chat',reply='哼，主人，我替你看过啦。')=>({type:'text',text:JSON.stringify({action,reply,confidence:.99})});
const defaults={currentSelection(){return {provider:'deepseek-account',model:'configured',reasoningEffort:'high'};}};

test('task view strips harness, reasoning, raw tool content and preserves terminal evidence',()=>{
  const view=taskView(fixture,'one');
  assert.equal(view.outcome,'completed'); assert.equal(view.state,'success'); assert.equal(view.toolsDone,1);
  assert.equal(view.latestUser,'修复显示问题'); assert.equal(view.latestReply,'修改完成，检查通过。');
  assert.doesNotMatch(JSON.stringify(view),/SECRET-|PRIVATE-/);
  const ongoing=taskView({...fixture,events:fixture.events.slice(0,-1)},'one');
  assert.notEqual(ongoing.outcome,'completed');
  assert.equal(taskView({...fixture,events:[...fixture.events,e(7,'turn/end',{reason:{kind:'canceled'}})]},'one').outcome,'canceled');
  assert.equal(taskTool(taskView({...fixture,events:[...fixture.events,e(7,'turn/start'),e(8,'approval/asked',{id:'approval'})]},'one'),'read_task_status').needsApproval,true);
});

test('chat reads only captured session and calls model without agent loop, thinking or harness',async()=>{
  let inspected=[];
  const llm=fakeLlm([[{type:'tool-call',id:'t',name:'read_task_progress',arguments:'{}'}],[answer()]]);
  const chat=new CompanionChat({async inspect(id){inspected.push(id);return fixture;},prompt(){throw Error('must not execute');}},
    {llm,agentDefaultModel:defaults,personaFile});
  const result=await chat.chat({text:'做完了吗',sessionId:'one',history:[]},new AbortController().signal);
  assert.deepEqual(inspected,['one']); assert.deepEqual(result.toolsUsed,['read_task_progress']);
  assert.equal(result.thinking,'off'); assert.equal(llm.requests[0].reasoningEffort,'off');
  assert.equal(llm.requests[0].maxTokens,640); assert.equal(llm.requests[0].sessionId,undefined);
  assert.doesNotMatch(JSON.stringify(llm.requests),/SECRET-|PRIVATE-|reasoning-delta/);
  assert.ok(result.contextChars<=LIMITS.inputChars);
});

test('daily chat does not inspect a task; proactive report cannot dispatch',async()=>{
  const llm=fakeLlm([[answer()],[answer('task')]]);
  const chat=new CompanionChat({inspect(){throw Error('daily chat inspected task');}}, {llm,agentDefaultModel:defaults,personaFile});
  assert.equal((await chat.chat({text:'今天想吃什么'},new AbortController().signal)).action,'chat');
  assert.equal((await chat.chat({text:'',event:'report'},new AbortController().signal)).action,'chat');
});

test('cross-session and unsupported tools are denied, even when model asks',async()=>{
  let reads=0;
  const llm=fakeLlm([[{type:'tool-call',id:'a',name:'read_task_reply',arguments:'{"sessionId":"other"}'},
    {type:'tool-call',id:'b',name:'bash',arguments:'{}'}],[answer()]]);
  const chat=new CompanionChat({inspect(){reads++;}}, {llm,agentDefaultModel:defaults,personaFile});
  await chat.chat({text:'现在怎么样',sessionId:'one'},new AbortController().signal);
  assert.equal(reads,0); assert.equal(llm.requests[1].messages.filter(m=>m.role==='tool').length,2);
  assert.ok(llm.requests[1].messages.filter(m=>m.role==='tool').every(m=>m.isError));
});

test('bounded context and credential filtering; no silent thinking fallback',async()=>{
  const context=buildContext('人设'.repeat(4000),{text:'你好'.repeat(4000),summary:'旧'.repeat(3000),
    facts:Array.from({length:100},()=>({key:'偏好',text:'蓝'.repeat(500)})),
    history:Array.from({length:100},(_,i)=>({role:i%2?'user':'assistant',text:'sk-123456789012345678901234567890 '+ '鱼'.repeat(2000)}))},{});
  assert.ok(JSON.stringify(context).length<LIMITS.inputChars); assert.doesNotMatch(JSON.stringify(context),/sk-1234/);
  const llm=fakeLlm([[answer()]],{efforts:['high']});
  const chat=new CompanionChat({}, {llm,agentDefaultModel:defaults,personaFile});
  await assert.rejects(chat.chat({text:'你好'},new AbortController().signal),/关闭思考/);
  assert.equal(llm.requests.length,0);
});

test('low-confidence task inference never executes',async()=>{
  const llm=fakeLlm([[{type:'text',text:'{"reply":"要我开始修改吗？","action":"task","confidence":0.6}'}]]);
  const chat=new CompanionChat({}, {llm,agentDefaultModel:defaults,personaFile});
  assert.equal((await chat.chat({text:'这个设计怎么样'},new AbortController().signal)).action,'chat');
});

test('independent emotion classification is tiny, stateless, tool-free and defaults to none',async()=>{
  const llm=fakeLlm(['shy','tail','unknown','none'].map(text=>[{type:'text',text}]));
  const chat=new CompanionChat({}, {llm,agentDefaultModel:defaults,personaFile});
  for (const [text,emotion] of [['你真好看','shy'],['摸尾巴','tail'],['讨论一下','none'],['今天星期几','none']])
    assert.equal((await chat.emotion(text,new AbortController().signal)).emotion,emotion);
  for (const request of llm.requests) {
    assert.equal(request.maxTokens,32); assert.equal(request.reasoningEffort,'off');
    assert.equal(request.tools,undefined); assert.equal(request.messages.length,2);
    assert.ok(request.messages[0].content[0].text.includes('默认 none'));
    assert.doesNotMatch(JSON.stringify(request),/SECRET-|PRIVATE-/);
  }
});

test('chat returns while concurrent emotion request is still pending',async()=>{
  let release;
  const blocked=new Promise(resolve=>release=resolve);
  const llm={async resolveModelInfo(){return {reasoning:{efforts:[{id:'off'}]}};},
    async *stream(request) {
      const emotion=request.maxTokens===32;
      if(emotion) await blocked;
      yield {type:'block-end',index:0,block:emotion?{type:'text',text:'none'}:answer()};
      yield {type:'finish',reason:{kind:'stop'}};
    }};
  const chat=new CompanionChat({}, {llm,agentDefaultModel:defaults,personaFile});
  let classified=false;
  const emotion=chat.emotion('普通聊天').then(result=>{classified=true;return result;});
  const reply=await chat.chat({text:'普通聊天'});
  assert.equal(reply.action,'chat'); assert.equal(classified,false); assert.equal(reply.emotion,undefined);
  release(); assert.equal((await emotion).emotion,'none');
});

test('provider preamble is removed from structured decision and bubble text',async()=>{
  const llm=fakeLlm([[{type:'text',text:'收到，我先看看。\n\n'+JSON.stringify({reply:'我准备帮你修复显示问题。',action:'task',confidence:.95})}]]);
  const chat=new CompanionChat({}, {llm,agentDefaultModel:defaults,personaFile});
  const result=await chat.chat({text:'帮我修复显示问题'},new AbortController().signal);
  assert.equal(result.action,'task'); assert.equal(result.reply,'我准备帮你修复显示问题。');
  assert.doesNotMatch(result.reply,/confidence|action|\{/);
});

test('chat preamble without confidence retains reply; malformed JSON fails without dispatch',async()=>{
  const llm=fakeLlm([[{type:'text',text:'我看看哦。\n{"reply":"主人想让我看哪个设计呀？","action":"chat"}'}],
    [{type:'text',text:'{"reply":"bad","action":"task",'}]]);
  const chat=new CompanionChat({}, {llm,agentDefaultModel:defaults,personaFile});
  const result=await chat.chat({text:'这个设计怎么样'},new AbortController().signal);
  assert.equal(result.action,'chat');assert.equal(result.reply,'主人想让我看哪个设计呀？');
  await assert.rejects(chat.chat({text:'帮我修改设计'},new AbortController().signal),/回复格式异常/);
});

test('workspace-attached creation, old-session recovery, authenticated compact task query',async()=>{
  const folder=await mkdtemp(resolve(tmpdir(),'fish-bridge-'));
  const calls=[]; const workspace={id:'workspace',path:'Y:/project',title:'项目',sessionIds:[],async attachSession(id){this.sessionIds.push(id);calls.push(['attach',id]);}};
  const registry={list(){return [workspace];},async resolveByPath(){return workspace;},async create(){throw Error('duplicate workspace');}};
  const api={async create(value){calls.push(['create',value]);workspace.sessionIds.push('new');return {sessionId:'new'};},
    async rename(value){calls.push(['rename',value]);return {title:value.title};},async inspect(id){return fixture;},
    async list(){return {items:[{sessionId:'new',cwd:'Y:/project',running:false}]};}};
  const bridge=await createBridge(api,{workspaceRegistry:registry,dataFile:resolve(folder,'connection.json')});
  try {
    const record=JSON.parse(await readFile(bridge.dataFile,'utf8'));
    const headers={'Authorization':'Bearer '+record.token,'content-type':'application/json'};
    const request=async(route,body)=>(await fetch(bridge.url+route,{headers,...body?{method:'POST',body:JSON.stringify(body)}:{}})).json();
    const created=await request('/session',{cwd:'Y:/project',title:'蓝色大肥鱼 · 修复'});
    assert.equal(created.workspaceId,'workspace'); assert.deepEqual(calls[0],['create',{workspaceId:'workspace'}]);
    await request('/attach',{sessionId:'old'}); assert.ok(workspace.sessionIds.includes('old'));
    assert.equal((await request('/sessions')).items[0].workspaceId,'workspace');
    const view=await request('/task-view?sessionId=new'); assert.equal(view.outcome,'completed'); assert.doesNotMatch(JSON.stringify(view),/SECRET-|PRIVATE-/);
    assert.equal((await fetch(bridge.url+'/task-view?sessionId=new')).status,401);
  } finally {await bridge.close();await rm(folder,{recursive:true,force:true});}
});

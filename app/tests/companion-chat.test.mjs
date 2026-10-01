import {test} from 'node:test';
import assert from 'node:assert/strict';
import {CompanionChat,taskView,taskTool,buildContext,LIMITS,readTaskView,toolState,partialChatReply,isClearlyCasual} from '../dsh_plugin/companion-chat.mjs';
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
  const result=await chat.chat({text:'能替我看一眼吗',sessionId:'one',history:[]},new AbortController().signal);
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
  const reads=[];
  const llm=fakeLlm([[{type:'tool-call',id:'a',name:'read_task_reply',arguments:'{"sessionId":"other"}'},
    {type:'tool-call',id:'b',name:'bash',arguments:'{}'}],[answer()]]);
  const chat=new CompanionChat({inspect(id){reads.push(id);return fixture;}}, {llm,agentDefaultModel:defaults,personaFile});
  await chat.chat({text:'替我看看',sessionId:'one'},new AbortController().signal);
  assert.deepEqual(reads,[]); assert.equal(llm.requests[1].messages.filter(m=>m.role==='tool').length,2);
  assert.ok(llm.requests[1].messages.filter(m=>m.role==='tool').every(m=>m.isError));
});

test('progress is read before the first model request even when no tool is called',async()=>{
  const reads=[];
  const ongoing={...fixture,events:[...fixture.events,e(7,'turn/start'),e(8,'user/message',{content:[{type:'text',text:'继续'}]}),
    e(9,'tool/call',{callId:'real-edit',name:'str_replace_editor',arguments:{command:'str_replace',payload:'SECRET-ARGUMENT'}})]};
  const llm=fakeLlm([[answer()]]);
  const chat=new CompanionChat({async inspect(id){reads.push(id);return ongoing;},async list(){return {items:[{sessionId:'one',running:true}]};}},
    {llm,agentDefaultModel:defaults,personaFile});
  const result=await chat.chat({text:'任务进度怎么样',sessionId:'one',history:[{role:'assistant',text:'没有任务在跑，我只能看到空白。'}]});
  assert.deepEqual(reads,['one']);assert.deepEqual(result.toolsUsed,['read_task_progress']);assert.equal(llm.requests.length,1);
  assert.equal(llm.requests[0].tools,undefined);assert.equal(result.modelCalls,1);
  const context=llm.requests[0].messages[0].content[0].text;
  assert.match(context,/"running":true/);assert.match(context,/"state":"coding"/);assert.match(context,/修复显示问题/);
  assert.doesNotMatch(context,/SECRET-|PRIVATE-/);
  assert.equal(result.action,'chat');assert.ok(result.contextChars<=LIMITS.inputChars);
});

test('chat and emotion follow the selected session model rather than the deployment default',async()=>{
  let inspections=0;
  const api={async list(){return {items:[{sessionId:'one',projections:{values:{modelSelection:{
    lastUsed:{provider:'old',model:'old'},next:{provider:'codebuddy',model:'deepseek-v4.1-flash',reasoningEffort:'high'}
  }}}}]};},inspect(){inspections++;throw Error('model selection must not read harness');}};
  const llm=fakeLlm([[answer()],[{type:'text',text:'none'}]]);
  const chat=new CompanionChat(api,{llm,agentDefaultModel:defaults,personaFile});
  const result=await chat.chat({text:'你好',sessionId:'one'});
  await chat.emotion('你好',undefined,'one');
  assert.equal(result.provider,'codebuddy');assert.equal(result.model,'deepseek-v4.1-flash');
  for(const request of llm.requests){assert.equal(request.provider,'codebuddy');assert.equal(request.model,'deepseek-v4.1-flash');assert.equal(request.reasoningEffort,'off');}
  assert.equal(inspections,0);
});

test('bound model lookup reads one current snapshot and closes before agent activation without listing sessions',async()=>{
  let closed=0,activated=0,reads=0,model='first';
  const api={list(){throw Error('whole session catalog must not be read');},
    async *follow(request,signal){
      reads++;assert.deepEqual(request,{address:{kind:'session',sessionId:'one'},maxMessages:1});
      assert.equal(signal.aborted,false);
      try {
        yield {type:'snapshot',header:{id:'one'},records:[{secret:'PRIVATE-HISTORY'}],projections:{values:{
          modelSelection:{next:{provider:'codebuddy',model,reasoningEffort:'high'}}}}};
        activated++;
      } finally {closed++;}
    }};
  const llm=fakeLlm([[answer()],[{type:'text',text:'none'}]]);
  const chat=new CompanionChat(api,{llm,agentDefaultModel:defaults,personaFile});
  assert.equal((await chat.chat({text:'你好',sessionId:'one'})).model,'first');
  model='second';await chat.emotion('我很好奇',undefined,'one');
  assert.equal(llm.requests[1].model,'second');
  assert.equal(reads,2);assert.equal(closed,2);assert.equal(activated,0);
  assert.doesNotMatch(JSON.stringify(llm.requests),/PRIVATE-HISTORY/);
});

test('empty or mismatched snapshot never falls back to a different session model',async()=>{
  for(const frame of [null,{type:'snapshot',header:{id:'other'}}]) {
    let closed=0;
    const api={list(){throw Error('must not fall back');},async *follow(){try {if(frame)yield frame;}finally{closed++;}}};
    const llm=fakeLlm([[answer()]]),chat=new CompanionChat(api,{llm,agentDefaultModel:defaults,personaFile});
    await assert.rejects(chat.chat({text:'你好',sessionId:'one'}),/会话/);
    assert.equal(closed,1);assert.equal(llm.requests.length,0);
  }
});

test('no bound session uses the default; failure to find a bound session never switches silently',async()=>{
  const llm=fakeLlm([[answer()]]);
  const chat=new CompanionChat({async list(){return {items:[]};}},{llm,agentDefaultModel:defaults,personaFile});
  assert.equal((await chat.chat({text:'你好'})).provider,'deepseek-account');
  await assert.rejects(chat.chat({text:'你好',sessionId:'missing'}),/会话.*模型/);
  assert.equal(llm.requests.length,1);
});

test('clearly casual chat uses a smaller read-only context while work and references retain the full path',async()=>{
  for(const text of ['今天想吃什么？用一句话回答。','你好呀','你是谁','我们聊聊天吧']) assert.equal(isClearlyCasual({text}),true,text);
  for(const text of ['你好，帮我修复文件','你是谁，统计一下项目数据','今天想吃什么，生成一个菜单文件','你上次说的是什么','任务进度怎么样'])
    assert.equal(isClearlyCasual({text}),false,text);
  const history=Array.from({length:12},(_,i)=>({role:i%2?'assistant':'user',text:'日常记录'.repeat(120)}));
  const llm=fakeLlm([[answer('task')]]);
  const chat=new CompanionChat({inspect(){throw Error('casual chat must not read task');}}, {llm,agentDefaultModel:defaults,personaFile});
  const result=await chat.chat({text:'今天想吃什么',history,facts:[{key:'称呼',text:'舰长'}]});
  assert.equal(result.action,'chat');assert.equal(llm.requests[0].tools,undefined);
  assert.equal(llm.requests[0].maxTokens,LIMITS.dailyMaxTokens);
  assert.ok(llm.requests[0].messages.length<=8);
  assert.match(llm.requests[0].messages[0].content[0].text,/舰长/);
  assert.ok(result.contextChars<4000);
});

test('stream preview decodes only chat prose and never leaks JSON syntax or incomplete escapes',()=>{
  const prefix='{"action":"chat","confidence":0.95,"reply":"';
  assert.equal(partialChatReply(prefix+'主人，'),'主人，');
  assert.equal(partialChatReply(prefix+'你好\\u'),'你好');
  assert.equal(partialChatReply(prefix+'你好\\u9c'),'你好');
  assert.equal(partialChatReply(prefix+'你好\\u9c7c\\n'),'你好鱼\n');
  assert.equal(partialChatReply(prefix+'你好\\"主人\\""}'),'你好"主人"');
  assert.equal(partialChatReply('{"action":"task","confidence":0.99,"reply":"准备好了'), '');
  assert.equal(partialChatReply('{"reply":"你好","action":"chat"}'), '你好');
  assert.equal(partialChatReply('{"reply":"你好\\'), '你好');
  assert.equal(partialChatReply('主人，我今天想吃小点心。'), '主人，我今天想吃小点心。');
  assert.equal(partialChatReply('收到。\n```json\n{"action":"chat"'), '收到。\n');
  assert.equal(partialChatReply('```json\n{"action":"chat"'), '');
});

test('HTTP chat preview arrives before generation finishes; no task is dispatched by a preview',async()=>{
  const folder=await mkdtemp(resolve(tmpdir(),'fish-stream-'));
  let release,finished=false,mutations=0;
  const waiting=new Promise(resolve=>{release=resolve;});
  const llm={async resolveModelInfo(){return {reasoning:{efforts:[{id:'off'}]}};},async *stream(){
    yield {type:'text-delta',index:0,text:'{"action":"chat","confidence":0.9,"reply":"主人，'};
    await waiting;finished=true;
    yield {type:'text-delta',index:0,text:'你好呀。"}'};
    yield {type:'finish',reason:{kind:'stop'}};
  }};
  const api={async list(){return {items:[]};},prompt(){mutations++;}};
  const bridge=await createBridge(api,{llm,agentDefaultModel:defaults,dataFile:resolve(folder,'connection.json')});
  try {
    const record=JSON.parse(await readFile(bridge.dataFile,'utf8'));
    const response=await fetch(bridge.url+'/chat',{method:'POST',headers:{'Authorization':'Bearer '+record.token,'content-type':'application/json'},
      body:JSON.stringify({text:'你好',turnId:'preview-turn',stream:true})});
    assert.match(response.headers.get('content-type'),/ndjson/);
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
    async function nextFrame(){
      while(!buffer.includes('\n')){const {value,done}=await reader.read();assert.equal(done,false);buffer+=decoder.decode(value,{stream:true});}
      const end=buffer.indexOf('\n'),line=buffer.slice(0,end);buffer=buffer.slice(end+1);return JSON.parse(line);
    }
    assert.equal((await nextFrame()).type,'start');
    const preview=await nextFrame();assert.equal(preview.type,'reply');assert.equal(preview.reply,'主人，');
    assert.equal(finished,false);assert.equal(mutations,0);
    release();let result;
    do {result=await nextFrame();} while(result.type!=='result');
    assert.equal(result.value.reply,'主人，你好呀。');assert.equal(result.value.action,'chat');assert.equal(mutations,0);
    await reader.cancel();
  } finally {release();await bridge.close();await rm(folder,{recursive:true,force:true});}
});

test('runtime status clears an abandoned thinking turn and never invents completion',async()=>{
  const open={...fixture,events:[...fixture.events,e(7,'turn/start'),e(8,'tool/call',{callId:'stale',name:'bash'})]};
  const api={async inspect(){return open;},async list(){return {items:[{sessionId:'one',running:false}]};}};
  const view=await readTaskView(api,'one');
  assert.equal(view.running,false);assert.equal(view.state,'idle');assert.equal(view.outcome,'unknown');assert.deepEqual(view.pendingCalls,[]);
  api.list=async()=>({items:[{sessionId:'one',running:true}]});
  const live=await readTaskView(api,'one');assert.equal(live.state,'executing');assert.equal(live.pendingTools[0],'bash');
  assert.equal(toolState('str_replace_editor',{command:'view'}),'reading');
});

test('failed status reads provide an unavailable snapshot without a false idle assertion',async()=>{
  const llm=fakeLlm([[answer()]]);
  const chat=new CompanionChat({async inspect(){throw Error('read unavailable');}}, {llm,agentDefaultModel:defaults,personaFile});
  await chat.chat({text:'任务进度',sessionId:'one'});
  const context=llm.requests[0].messages[0].content[0].text;
  assert.match(context,/"available":false/);assert.match(context,/不能说任务没有运行/);assert.doesNotMatch(context,/"running":false/);
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

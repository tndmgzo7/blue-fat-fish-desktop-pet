import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {dirname, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createBridge} from '../dsh_plugin/index.mjs';
test('authenticated bridge, delivery, stream and cleanup', async () => {
  const root = dirname(fileURLToPath(import.meta.url));
  const folder = await mkdtemp(resolve(root, 'bridge-test-'));
  let prompt, canceled;
  const api = {
    async list() { return {items:[{sessionId:'one',running:true,cwd:'Y:/项目',projections:{values:{title:'测试会话'}}}]}; },
    async create(value) { assert.equal(value.cwd,'Y:/项目'); return {sessionId:'new'}; },
    async prompt(value) { prompt = value; return {accepted:true}; },
    async cancel(value) { canceled = value.sessionId; return {accepted:true}; },
    async *follow(value,signal) {
      assert.equal(value.address.sessionId,'one'); assert.equal(value.assistantStream,true);
      yield {type:'snapshot',cursor:0,records:[]};
      yield {type:'event',event:{seq:1,type:'turn/start',data:{}}};
      if (!signal.aborted) await new Promise(done => signal.addEventListener('abort',done,{once:true}));
    }
  };
  const bridge = await createBridge(api,{dataFile:resolve(folder,'connection.json')});
  try {
    const record = JSON.parse(await readFile(bridge.dataFile,'utf8'));
    const headers = {Authorization:'Bearer '+record.token,'content-type':'application/json'};
    const request = (route,body) => fetch(bridge.url+route,{headers,...(body?{method:'POST',body:JSON.stringify(body)}:{})});
    assert.equal((await fetch(bridge.url+'/sessions')).status,401);
    assert.equal((await fetch(bridge.url+'/health',{headers:{...headers,Origin:'http://example.test'}})).status,401);
    assert.equal((await (await request('/sessions')).json()).items[0].title,'测试会话');
    assert.equal((await (await request('/session',{cwd:'Y:/项目'})).json()).sessionId,'new');
    assert.equal((await request('/prompt',{sessionId:'one',requestId:'request-1',text:'你好',mode:'steer'})).status,200);
    assert.deepEqual(prompt.content,[{type:'text',text:'你好'}]); assert.equal(prompt.mode,'steer');
    assert.equal(prompt.requestId,'request-1');
    assert.equal((await request('/prompt',{sessionId:'one',requestId:'x',text:'x',mode:'unsafe'})).status,400);
    await request('/cancel',{sessionId:'one'}); assert.equal(canceled,'one');
    const reader = (await request('/follow?sessionId=one')).body.getReader();
    let output = '';
    while (!output.includes('turn/start')) output += new TextDecoder().decode((await reader.read()).value);
    assert.equal(JSON.parse(output.trim().split('\n')[0]).type,'snapshot');
    await reader.cancel(); await bridge.close(); await assert.rejects(readFile(bridge.dataFile));
  } finally {
    await bridge.close();
    if (!resolve(folder).startsWith(resolve(root)+sep)) throw new Error('Unsafe cleanup path');
    await rm(folder,{recursive:true,force:true});
  }
});
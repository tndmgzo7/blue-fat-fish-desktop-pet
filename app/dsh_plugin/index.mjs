import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { mkdir, writeFile, rename, readFile, unlink } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {CompanionChat,taskView} from './companion-chat.mjs';

const defaultFile = resolve(dirname(fileURLToPath(import.meta.url)), '../userdata/dsh-bridge.json');

function json(response, code, value) {
  response.writeHead(code, {'content-type':'application/json; charset=utf-8', 'cache-control':'no-store'});
  response.end(JSON.stringify(value));
}

async function bodyOf(request) {
  if (!String(request.headers['content-type'] || '').startsWith('application/json')) throw new Error('JSON request required');
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 128 * 1024) throw new Error('Request too large');
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Object required');
  return value;
}

export async function createBridge(api, options = {}) {
  const token = randomBytes(32).toString('hex');
  const dataFile = options.dataFile || defaultFile;
  const lifetime = new AbortController();
  const streams = new Set();
  const sockets = new Set();
  const registry=options.workspaceRegistry;
  const chat=new CompanionChat(api,{llm:options.llm,agentDefaultModel:options.agentDefaultModel,
    personaFile:options.personaFile||fileURLToPath(new URL('./persona.md',import.meta.url))});
  const chatTurns=new Set();
  const emotionTurns=new Set();
  async function workspaceFor(path) {
    if (!registry) throw new Error('dsh 未提供工作区接口，无法创建可见会话。');
    return await registry.resolveByPath(path) || await registry.create(path);
  }
  let closed = false;
  const server = createServer(async (request, response) => {
    const presented = String(request.headers.authorization || '');
    const expected = 'Bearer ' + token;
    if (request.headers.origin || Buffer.byteLength(presented) !== Buffer.byteLength(expected) ||
        !timingSafeEqual(Buffer.from(presented), Buffer.from(expected))) return json(response, 401, {error:'Unauthorized'});
    const url = new URL(request.url, 'http://127.0.0.1');
    const cancellation = new AbortController();
    const stop = () => cancellation.abort();
    response.once('close', stop);
    lifetime.signal.addEventListener('abort', stop, {once:true});
    try {
      if (request.method === 'GET' && url.pathname === '/health') {
        return json(response, 200, {apiVersion:2, build:'2026.09.30-reactions-2', name:'蓝色大肥鱼 dsh bridge', pid:process.pid,
          companionChat:!!options.llm, workspace:!!registry});
      }
      if (request.method === 'GET' && url.pathname === '/sessions') {
        const result = await api.list({}, cancellation.signal);
        const workspaces=registry?.list()||[];
        const items = result.items.slice(0, 100).map(row => {
          const title = row.projections?.values?.title;
          return {sessionId:row.sessionId, updatedAt:row.updatedAt, running:row.running,
                  title:typeof title === 'string' ? title : typeof title?.text === 'string' ? title.text : '',
                  cwd:row.cwd || '', parentSessionId:row.parentSessionId || null,
                  workspaceId:workspaces.find(w=>w.sessionIds.includes(row.sessionId))?.id||null,
                  archived:registry?.archivedSessionIds?.includes(row.sessionId)||false};
        });
        return json(response, 200, {items});
      }
      if (request.method==='GET' && url.pathname==='/task-view') {
        const identity=url.searchParams.get('sessionId');
        if (!identity||identity.length>512) throw new Error('Session required');
        return json(response,200,taskView(await api.inspect(identity,cancellation.signal),identity));
      }
      if (request.method === 'GET' && url.pathname === '/follow') {
        const sessionId = url.searchParams.get('sessionId');
        if (!sessionId || sessionId.length > 512) throw new Error('Session required');
        const iterable = api.follow({address:{kind:'session', sessionId}, maxMessages:12, assistantStream:true}, cancellation.signal);
        streams.add(cancellation);
        response.writeHead(200, {'content-type':'application/x-ndjson; charset=utf-8', 'cache-control':'no-store'});
        response.flushHeaders();
        const heartbeat = setInterval(() => { if (!response.destroyed) response.write('\n'); }, 15000);
        try {
          for await (const frame of iterable) {
            if (response.destroyed || cancellation.signal.aborted) break;
            if (!response.write(JSON.stringify(frame) + '\n')) {
              await new Promise(resolve => {
                response.once('drain', resolve);
                response.once('close', resolve);
              });
            }
          }
        } finally {
          clearInterval(heartbeat);
          streams.delete(cancellation);
        }
        return response.end();
      }
      if (request.method === 'POST') {
        const body = await bodyOf(request);
        if (url.pathname==='/emotion') {
          const identity=String(body.turnId||'');
          if (typeof body.text!=='string'||!body.text.trim()||body.text.length>4000||!identity||identity.length>128) throw new Error('Emotion input required');
          if(emotionTurns.size>=2||emotionTurns.has(identity)) return json(response,200,{emotion:'none'});
          emotionTurns.add(identity);
          const timeout=setTimeout(stop,10000);
          try { return json(response,200,await chat.emotion(body.text,cancellation.signal)); }
          finally {clearTimeout(timeout);emotionTurns.delete(identity);}
        }
        if (url.pathname==='/chat') {
          if (typeof body.text!=='string'||body.text.length>32000||(!body.text.trim()&&body.event!=='report')) throw new Error('请输入聊天内容。');
          const identity=String(body.turnId||'');
          if (!identity||identity.length>128) throw new Error('Chat turn required');
          if (chatTurns.size>=2||chatTurns.has(identity)) throw new Error('我还在回答这句话呢，稍等一下。');
          chatTurns.add(identity);
          const timeout=setTimeout(stop,45000);
          try { return json(response,200,await chat.chat(body,cancellation.signal)); }
          finally { clearTimeout(timeout); chatTurns.delete(identity); }
        }
        if (url.pathname === '/session') {
          if (typeof body.cwd !== 'string' || !body.cwd.trim()) throw new Error('Working folder required');
          const workspace=await workspaceFor(body.cwd);
          const value=await api.create({workspaceId:workspace.id});
          let title='';
          if (typeof body.title==='string'&&body.title.trim()&&api.rename) {
            try { title=(await api.rename({sessionId:value.sessionId,title:body.title.trim().slice(0,100)})).title; } catch {}
          }
          return json(response, 200, {...value,workspaceId:workspace.id,workspaceTitle:workspace.title,title,cwd:workspace.path});
        }
        if (typeof body.sessionId !== 'string' || !body.sessionId) throw new Error('Session required');
        if (url.pathname==='/attach') {
          const inspected=await api.inspect(body.sessionId,cancellation.signal);
          const workspace=await workspaceFor(inspected.meta?.cwd);
          await workspace.attachSession(body.sessionId);
          return json(response,200,{sessionId:body.sessionId,workspaceId:workspace.id,workspaceTitle:workspace.title,cwd:workspace.path});
        }
        if (url.pathname === '/prompt') {
          if (typeof body.text !== 'string' || !body.text.trim() || body.text.length > 32000) throw new Error('Prompt must contain 1–32000 characters');
          if (typeof body.requestId !== 'string' || !body.requestId || body.requestId.length > 128) throw new Error('Request identity required');
          if (!['queue','steer'].includes(body.mode)) throw new Error('Invalid delivery mode');
          return json(response, 200, await api.prompt({sessionId:body.sessionId, requestId:body.requestId,
            mode:body.mode, content:[{type:'text',text:body.text}], clientTimeZone:body.clientTimeZone || 'Asia/Shanghai'}, cancellation.signal));
        }
        if (url.pathname === '/cancel') return json(response, 200, await api.cancel({sessionId:body.sessionId}));
      }
      return json(response, 404, {error:'Unknown route'});
    } catch (error) {
      if (!response.destroyed && !response.headersSent) json(response, 400, {error:String(error.message || error), code:error.code || null});
      else if (!response.destroyed && !cancellation.signal.aborted) response.end(JSON.stringify({type:'bridge-error', error:String(error.message || error)}) + '\n');
    } finally {
      lifetime.signal.removeEventListener('abort', stop);
    }
  });
  server.on('connection', socket => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  server.requestTimeout = 10000;
  const close = async () => {
    if (closed) return;
    closed = true;
    lifetime.abort();
    for (const controller of streams) controller.abort();
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    try {
      const record = JSON.parse(await readFile(dataFile, 'utf8'));
      if (record.token === token) await unlink(dataFile);
    } catch {}
  };
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(options.port || 0, '127.0.0.1', resolve);
    });
    const record = {apiVersion:1, url:`http://127.0.0.1:${server.address().port}`, token, pid:process.pid};
    await mkdir(dirname(dataFile), {recursive:true});
    const temporary = dataFile + '.' + process.pid + '.tmp';
    await writeFile(temporary, JSON.stringify(record), {mode:0o600});
    await rename(temporary, dataFile);
    return {close, url:record.url, dataFile};
  } catch (error) {
    await close();
    throw error;
  }
}

export default {
  name:'whale-pet-bridge',
  inject:['sessionController','llm','agentDefaultModel','workspaceRegistry'],
  async apply(ctx, config = {}) {
    const bridge = await createBridge(ctx.get('sessionController'), {...config,llm:ctx.get('llm'),
      agentDefaultModel:ctx.get('agentDefaultModel'),workspaceRegistry:ctx.get('workspaceRegistry')});
    ctx.effect(function* () { yield () => bridge.close(); });
    ctx.logger.info('Whale Pet local bridge is ready.');
  }
};

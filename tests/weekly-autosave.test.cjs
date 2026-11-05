const test=require('node:test');
const assert=require('node:assert/strict');
const {createWeeklyAutosave}=require('../static/weekly-reports.js');
const clone=value=>JSON.parse(JSON.stringify(value));
const draft=()=>({id:1,status:'pending',edit_revision:0,editorial:{summary:'初始摘要',notes:{},orders:{}}});
const response=(model,payload)=>({...clone(model),status:'pending',edit_revision:payload.edit_revision+1,
  editorial:Object.fromEntries(Object.entries(payload).filter(([key])=>!['action','edit_revision'].includes(key)))});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('debounces consecutive typing into one saved draft',async()=>{
  const model=draft(),calls=[],states=[];
  const autosave=createWeeklyAutosave({delay:5,save:async(id,payload)=>{calls.push(payload);return response(model,payload);},onState:s=>states.push(s)});
  autosave.reset(model);model.editorial.summary='第一版';autosave.edit();model.editorial.summary='最终输入';autosave.edit();
  await new Promise(resolve=>setTimeout(resolve,25));await autosave.flush();
  assert.equal(calls.length,1);assert.equal(calls[0].summary,'最终输入');assert.equal(model.edit_revision,1);
  assert.equal(autosave.dirty,false);assert.equal(states.at(-1),'saved');
});

test('typing during a pending save is retained and saved with the new revision',async()=>{
  const model=draft(),first=deferred(),calls=[];
  const autosave=createWeeklyAutosave({save:async(id,payload)=>{calls.push(clone(payload));return calls.length===1?first.promise:response(model,payload);}});
  autosave.reset(model);model.editorial.summary='第一版';autosave.edit();const saving=autosave.flush();
  assert.strictEqual(autosave.flush(),saving);
  model.editorial.summary='保存期间继续输入';autosave.edit();
  first.resolve(response(model,calls[0]));assert.equal(await saving,true);
  assert.equal(calls.length,2);assert.equal(calls[1].edit_revision,1);assert.equal(calls[1].summary,'保存期间继续输入');
  assert.equal(model.editorial.summary,'保存期间继续输入');assert.equal(model.edit_revision,2);assert.equal(autosave.dirty,false);
});

test('save failure retains input and can be retried without creating another report',async()=>{
  const model=draft();let fail=true,calls=0;
  const autosave=createWeeklyAutosave({save:async(id,payload)=>{calls++;if(fail)throw new Error('网络中断');return response(model,payload);}});
  autosave.reset(model);model.editorial.summary='需要保留的内容';autosave.edit();
  assert.equal(await autosave.flush(),false);assert.equal(autosave.dirty,true);assert.equal(model.editorial.summary,'需要保留的内容');
  fail=false;assert.equal(await autosave.flush(),true);assert.equal(model.id,1);assert.equal(calls,2);assert.equal(autosave.error,null);
});

test('conflicts stop automatic retries and never overwrite the local input',async()=>{
  const model=draft();let calls=0;
  const autosave=createWeeklyAutosave({delay:1,save:async()=>{calls++;const e=new Error('其他人已编辑');e.status=409;throw e;}});
  autosave.reset(model);model.editorial.summary='本地修改';autosave.edit();assert.equal(await autosave.flush(),false);
  model.editorial.summary='冲突后继续整理';autosave.edit();await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(await autosave.flush(),false);assert.equal(calls,1);assert.equal(model.editorial.summary,'冲突后继续整理');
  assert.equal(autosave.error.status,409);assert.equal(autosave.dirty,true);
  autosave.reset({...draft(),edit_revision:2});assert.equal(autosave.dirty,false);assert.equal(autosave.error,null);
});

test('publishing waits for the final input and uses the saved edit revision',async()=>{
  const model=draft(),pending=deferred(),events=[];
  const autosave=createWeeklyAutosave({save:async(id,payload)=>{events.push(['save',clone(payload)]);return pending.promise;}});
  autosave.reset(model);model.editorial.summary='发布前的最后修改';autosave.edit();
  const publish=(async()=>{if(await autosave.flush())events.push(['publish',model.edit_revision,model.editorial.summary]);})();
  assert.equal(events.length,1);assert.equal(events[0][0],'save');
  pending.resolve(response(model,events[0][1]));await publish;
  assert.deepEqual(events[1],['publish',1,'发布前的最后修改']);
});

test('a failed save blocks publishing',async()=>{
  const model=draft();let published=false;
  const autosave=createWeeklyAutosave({save:async()=>{throw new Error('保存失败');}});
  autosave.reset(model);model.editorial.summary='未保存修改';autosave.edit();
  if(await autosave.flush())published=true;
  assert.equal(published,false);assert.equal(autosave.dirty,true);
});

test('late responses from another report or identity cannot replace the current draft',async()=>{
  const old=draft(),next={...draft(),id:2},pending=deferred();
  const autosave=createWeeklyAutosave({save:()=>pending.promise});
  autosave.reset(old);old.editorial.summary='旧身份内容';autosave.edit();const saving=autosave.flush();
  autosave.reset(next);pending.resolve({...old,edit_revision:1,editorial:{summary:'旧保存结果'}});
  assert.equal(await saving,false);assert.equal(next.editorial.summary,'初始摘要');assert.equal(next.edit_revision,0);assert.equal(autosave.dirty,false);
});

test('published snapshots cannot enter autosave',async()=>{
  const model={...draft(),status:'published'};let calls=0;
  const autosave=createWeeklyAutosave({save:async()=>{calls++;}});
  autosave.reset(model);autosave.edit();assert.equal(await autosave.flush(),true);assert.equal(calls,0);assert.equal(autosave.dirty,false);
});

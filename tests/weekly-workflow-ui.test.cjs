const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));

function browser(role='manager'){
  const elements=new Map();
  function element(){
    const e={children:[],listeners:{},className:'',value:'',textContent:'',hidden:false,disabled:false,
      append(x){this.children.push(x)},prepend(x){this.children.unshift(x)},insertBefore(x){this.children.unshift(x)},
      style:{setProperty(){}},setAttribute(){},focus(){},select(){},addEventListener(k,f){this.listeners[k]=f;}};
    Object.defineProperty(e,'id',{get(){return this._id},set(id){this._id=id;elements.set(id,this);}});
    e.classList={contains(c){return e.className.split(' ').includes(c)},add(c){e.className+=' '+c},remove(c){e.className=e.className.split(' ').filter(x=>x!==c).join(' ')},toggle(c,force){const add=force??!this.contains(c);add?this.add(c):this.remove(c);return add;}};
    Object.defineProperty(e,'innerHTML',{get(){return this._html||''},set(html){this._html=html;for(const match of html.matchAll(/<[a-z]+\b[^>]*\bid="([^"]+)"[^>]*>/g)){const child=element();child.id=match[1];child.className=match[0].match(/class="([^"]*)"/)?.[1]||'';child.hidden=/\shidden(?:\s|>)/.test(match[0]);child.disabled=/\sdisabled(?:\s|>)/.test(match[0]);}}});
    return e;
  }
  for(const id of ['homePage','taskPage','approvalPage','orgPage','performancePage','weeklyTab']){const el=element();el.id=id;}
  elements.get('weeklyTab').className='hidden';
  const nav=element(),document={head:element(),body:element(),hidden:false,createElement:element,addEventListener(){},querySelector(s){return s==='.nav-tabs'?nav:elements.get(s.slice(1))||null;},querySelectorAll(s){return s==='.nav-tabs button'?nav.children:[];}};
  const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date()),monday=new Date(today+'T00:00:00Z');monday.setUTCDate(monday.getUTCDate()-(monday.getUTCDay()+6)%7);
  let model={id:1,version:1,status:'pending',edit_revision:0,period_start:monday.toISOString().slice(0,10),period_end:today,generated_at:today,editorial:{summary:'自动摘要',conclusion:'',requirements:'',support:'',notes:{},orders:{}},snapshot:{title:'科技产品中心部门工作总结报告',summary:'自动摘要',data_cutoff:today,rules:{},tasks:[],sections:{achievements:[],risks:[],next_week:[]},metric_tasks:{},metrics:{new:0,completed:0,in_progress:0,overdue:0,awaiting_acceptance:0,domains:0,completed_hours:{standard:0,actual:0}}}};
  const calls=[],navigation=[],confirmations=[];let heldSave=null,failSave=false;
  const context={document,navigator:{},Intl,Date,URLSearchParams,console,setTimeout,clearTimeout,setInterval(){},currentUsers:[{id:1,role}],currentActorId:()=>1,renderDemoActor(){},openPage(name){navigation.push(name)},confirm(text){confirmations.push(text);return true},addEventListener(){},
    fetch:async(url,options)=>{
      const route=new URL(url,'http://test.local').pathname,body=options?JSON.parse(options.body):null;
      calls.push({route,body});
      if(route==='/api/weekly-reports/current')return {ok:true,json:async()=>clone(model)};
      if(route.endsWith('/changes'))return {ok:true,json:async()=>({has_changes:false,added:[],removed:[],changed:[]})};
      if(!body)return {ok:true,json:async()=>({items:[],pending:[],current_period_start:model.period_start})};
      if(body.action==='save'){
        const gate=heldSave;if(gate)await gate.promise;
        if(failSave)return {ok:false,status:503,json:async()=>({error:'测试网络中断'})};
        const {actor_id,action,edit_revision,...editorial}=body;model={...model,editorial,edit_revision:edit_revision+1};
      }else if(body.action==='publish')model={...model,status:'published',published_by:1,publisher_name:'负责人',published_at:today,edit_revision:body.edit_revision+1};
      return {ok:true,json:async()=>clone(model)};
    }};
  context.window=context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/weekly-reports.js'),'utf8'),context);
  const click=action=>elements.get('weeklyActions').listeners.click({target:{closest:()=>({dataset:{action}})}});
  const edit=value=>elements.get('weeklyContent').listeners.input({target:{dataset:{editorial:'summary'},value}});
  return {context,elements,calls,navigation,confirmations,click,edit,get model(){return model;},hold(){heldSave=deferred();return heldSave;},fail(value){failSave=value;}};
}

test('opening the report auto-loads current week with no generation, save or review buttons',async()=>{
  for(const role of ['manager','pm']){
    const b=browser(role);await b.context.openPage('weekly');await tick();
    assert(b.calls.some(c=>c.route==='/api/weekly-reports/current'&&c.body));
    assert(b.elements.get('weeklyContent').innerHTML.includes('科技产品中心部门工作总结报告'));
    const actions=b.elements.get('weeklyActions').innerHTML;
    assert.equal(actions.includes('data-action="publish"'),role==='manager');
    assert(!actions.includes('data-action="save"'));assert(!actions.includes('data-action="review"'));
    assert(!b.elements.has('weeklyDate'));assert(!b.elements.has('weeklyGenerate'));
  }
  const b=browser('member');await b.context.openPage('weekly');
  assert(b.elements.get('weeklyTab').classList.contains('hidden'));
  assert(!b.calls.some(c=>c.route==='/api/weekly-reports/current'));
});

test('one click publishes only after pending edits are saved, without a confirmation dialog',async()=>{
  const b=browser();await b.context.openPage('weekly');const held=b.hold();
  b.edit('即将发布的最后一版摘要');const publishing=b.click('publish');await tick();
  assert(b.calls.some(c=>c.body?.action==='save'));assert(!b.calls.some(c=>c.body?.action==='publish'));
  assert.equal(b.elements.get('weeklyContent').inert,true);
  held.resolve();await publishing;
  const saved=b.calls.find(c=>c.body?.action==='save'),published=b.calls.find(c=>c.body?.action==='publish');
  assert.equal(saved.body.summary,'即将发布的最后一版摘要');assert.equal(published.body.edit_revision,1);
  assert.equal(b.model.status,'published');assert.equal(b.confirmations.length,0);
  assert(!b.elements.get('weeklyActions').innerHTML.includes('data-action="publish"'));
});

test('failed autosave keeps the input, blocks publishing, and offers a retry',async()=>{
  const b=browser();await b.context.openPage('weekly');b.fail(true);b.edit('保存失败也不能丢的内容');await b.click('publish');
  assert(!b.calls.some(c=>c.body?.action==='publish'));assert.equal(b.elements.get('weeklyPublish').disabled,true);
  assert.equal(b.elements.get('weeklyRetry').hidden,false);
  b.fail(false);await b.click('retry');await b.click('publish');
  assert.equal(b.model.editorial.summary,'保存失败也不能丢的内容');assert.equal(b.model.status,'published');
});

test('legacy weekly tasks render in numeric order without domain groups and retain explicit manual ordering',async()=>{
  for(const custom of [null,[18,30,2]]){
    const b=browser();
    b.model.snapshot.tasks=[30,2,18].map(id=>({id,title:'编号排序 '+id,domain_name:id===2?'Z 责任域':'A 责任域',work_status:'in_progress',priority:'P0'}));
    b.model.snapshot.sections.achievements=[30,2,18];
    if(custom)b.model.editorial.orders.achievements=custom;
    await b.context.openPage('weekly');
    const html=b.elements.get('weeklyContent').innerHTML;
    assert(!html.includes('weekly-domain-group'));assert(!html.includes('weekly-domain-items'));
    assert.deepEqual([...html.matchAll(/<h4>#(\d+) /g)].map(match=>Number(match[1])),custom||[2,18,30]);
    assert(html.includes('Z 责任域'));
  }
});

test('leaving the page waits for autosave instead of asking to discard changes',async()=>{
  const b=browser();await b.context.openPage('weekly');const held=b.hold();b.edit('离开前的输入');
  const leaving=b.context.openPage('tasks');await tick();assert.equal(b.navigation.length,0);
  held.resolve();await leaving;assert.deepEqual(b.navigation,['tasks']);assert.equal(b.confirmations.length,0);
  assert.equal(b.model.editorial.summary,'离开前的输入');
});

test('task cards distinguish confirmed, pending and unavailable progress without equating 100% with acceptance',async()=>{
  const b=browser();
  b.model.snapshot.tasks=[
    {id:41,title:'方案联调',effective_percent:30,latest_progress:'填报80%，尚待确认',work_status:'in_progress',priority:'P0'},
    {id:42,title:'历史快照',latest_progress:'曾填报60%',work_status:'in_progress',priority:'P1'},
    {id:43,title:'等待验收',effective_percent:100,work_status:'awaiting_acceptance',priority:'P1'}
  ];
  b.model.snapshot.sections.achievements=[41,42,43];await b.context.openPage('weekly');
  const html=b.elements.get('weeklyContent').innerHTML;
  const card=id=>html.split(`data-task-id="${id}"`)[1].split('</article>')[0];
  assert(card(41).includes('aria-valuenow="30"'));assert(card(41).includes('填报80%，尚待确认'));
  assert(card(42).includes('暂无已确认进度'));assert(!card(42).includes('role="progressbar"'));
  assert(card(43).includes('aria-valuenow="100"'));assert(card(43).includes('待验收'));assert(!card(43).includes('status-completed'));
});

test('task card descriptions and editorial notes remain escaped and ordering controls remain available',async()=>{
  const b=browser(),unsafe='<img src=x onerror=alert(1)>';
  b.model.snapshot.tasks=[{id:44,title:unsafe,latest_progress:unsafe,block_reason:unsafe,work_status:'in_progress',priority:'P0',risk_reasons:['逾期未完成'],overdue:true}];
  b.model.snapshot.sections.risks=[44];b.model.editorial.notes[44]='</textarea>'+unsafe;
  await b.context.openPage('weekly');const html=b.elements.get('weeklyContent').innerHTML;
  assert(!html.includes(unsafe));assert(html.includes('&lt;img'));assert(html.includes('data-note="44"'));
  assert(html.includes('风险/最后进展'));assert(html.includes('data-move="-1"'));assert(html.includes('data-move="1"'));
});

test('legacy cards display the recovered cutoff percentage while explicit unknown snapshots stay unknown',async()=>{
  const b=browser();b.model.task_progress={'51':80,'52':95};
  b.model.snapshot.tasks=[{id:51,title:'旧周报',work_status:'in_progress',priority:'P0',latest_progress:'此任务完成80%'},
    {id:52,title:'明确未知的历史进度',work_status:'in_progress',priority:'P0',effective_percent:null}];
  b.model.snapshot.sections.achievements=[51,52];await b.context.openPage('weekly');
  const html=b.elements.get('weeklyContent').innerHTML;
  const first=html.split('data-task-id="51"')[1].split('</article>')[0],second=html.split('data-task-id="52"')[1].split('</article>')[0];
  assert(first.includes('aria-valuenow="80"'));assert(second.includes('暂无已确认进度'));
  assert(second.includes('class="weekly-progress-track"'));assert(!second.includes('aria-valuenow="95"'));
});

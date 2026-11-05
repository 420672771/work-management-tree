const test=require('node:test');
const assert=require('node:assert/strict');
const {layoutMonthlyMap,monthlyMapLines}=require('../static/monthly-reports.js');
const {createWeeklyAutosave}=require('../static/weekly-reports.js');
const node=(id,children=[])=>({id,text:'测试节点 '+id,children});
test('mindmap expands to the right without overlapping visible nodes',()=>{
 const root=node('root',Array.from({length:6},(_,i)=>node('branch'+i,Array.from({length:i+1},(_,j)=>node(i+':'+j,[node('leaf:'+i+':'+j)])))));
 const map=layoutMonthlyMap(root);
 const center=map.nodes.find(n=>n.depth===0);
 for(const edge of map.edges)assert(edge.child.x-edge.child.width/2>edge.parent.x+edge.parent.width/2);
 assert(map.nodes.every(n=>n.x>=center.x));
 for(const a of map.nodes)for(const b of map.nodes)if(a!==b){
  assert(!(Math.abs(a.x-b.x)<(a.width+b.width)/2&&Math.abs(a.y-b.y)<(a.height+b.height)/2),'overlapping '+a.node.id+' '+b.node.id);
 }
 assert.equal(map.edges.length,map.nodes.length-1);
 for(const value of Object.values(map.bounds))assert(Number.isFinite(value));
 const folded=layoutMonthlyMap(root,new Set(['root']));assert.equal(folded.nodes.length,1);
 assert(layoutMonthlyMap(root,new Set(['branch0'])).nodes.length<map.nodes.length);
});
test('long Chinese labels remain complete and wrap without ellipses',()=>{
 const long='这是一个很长的任务名称'.repeat(40);
 const complete=monthlyMapLines(long,180);assert(complete.length>3);assert.equal(complete.join(''),long);
 assert.deepEqual(monthlyMapLines('第一行\n\n第二行\n',180),['第一行','','第二行','']);
 const lines=monthlyMapLines(long,180,3);
 assert.equal(lines.length,3);assert(lines[2].endsWith('…'));
 assert.deepEqual(monthlyMapLines('第一行\n第二行',180),['第一行','第二行']);
});
test('monthly draft autosaves outline and archived/readonly reports cannot become dirty',async()=>{
 const calls=[],report={id:1,status:'draft',can_edit:true,edit_revision:0,editorial:{outline:node('root')}};
 const state=createWeeklyAutosave({save:async(id,body)=>{calls.push(body);return {...report,edit_revision:1};},canEdit:r=>r.can_edit&&r.status==='draft'});
 state.reset(report);report.editorial.outline.text='本月总结';state.edit();assert(await state.flush());
 assert.equal(calls[0].outline.text,'本月总结');assert.equal(report.edit_revision,1);
 state.reset({...report,status:'archived'});state.edit();assert.equal(state.dirty,false);
 state.reset({...report,can_edit:false});state.edit();assert.equal(state.dirty,false);
});

test('wide multi-line branches stay separated and the layout bounds include all text',()=>{
 const root=node('root',[{...node('wide',[node('child1'),node('child2')]),text:'完整的交付物和风险说明'.repeat(120)},node('short')]);
 const map=layoutMonthlyMap(root),long=map.nodes.find(n=>n.node.id==='wide');
 assert(long.lines.length>3);assert.equal(long.lines.join(''),long.node.text);
 for(const n of map.nodes){assert(n.x-n.width/2>=map.bounds.x);assert(n.x+n.width/2<=map.bounds.x+map.bounds.width);assert(n.y-n.height/2>=map.bounds.y);assert(n.y+n.height/2<=map.bounds.y+map.bounds.height);}
 for(const a of map.nodes)for(const b of map.nodes)if(a!==b)assert(!(Math.abs(a.x-b.x)<(a.width+b.width)/2&&Math.abs(a.y-b.y)<(a.height+b.height)/2));
});

const test=require('node:test');
const assert=require('node:assert/strict');
const {layoutMonthlyMap,monthlyMapLines}=require('../static/monthly-reports.js');
const {createWeeklyAutosave}=require('../static/weekly-reports.js');
const node=(id,children=[])=>({id,text:'测试节点 '+id,children});
test('mindmap branches extend to both sides without overlapping visible nodes',()=>{
 const root=node('root',Array.from({length:6},(_,i)=>node('branch'+i,Array.from({length:i+1},(_,j)=>node(i+':'+j,[node('leaf:'+i+':'+j)])))));
 const map=layoutMonthlyMap(root);
 assert(map.nodes.some(n=>n.x<0));assert(map.nodes.some(n=>n.x>0));
 for(const a of map.nodes)for(const b of map.nodes)if(a!==b){
  assert(!(Math.abs(a.x-b.x)<(a.width+b.width)/2&&Math.abs(a.y-b.y)<(a.height+b.height)/2),'overlapping '+a.node.id+' '+b.node.id);
 }
 assert.equal(map.edges.length,map.nodes.length-1);
 for(const value of Object.values(map.bounds))assert(Number.isFinite(value));
 const folded=layoutMonthlyMap(root,new Set(['root']));assert.equal(folded.nodes.length,1);
 assert(layoutMonthlyMap(root,new Set(['branch0'])).nodes.length<map.nodes.length);
});
test('long Chinese labels wrap into bounded visible lines',()=>{
 const lines=monthlyMapLines('这是一个很长的任务名称'.repeat(40),180);
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

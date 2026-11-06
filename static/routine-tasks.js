/* Weekly routine estimates belong to the template; actual hours belong to each week. */
(()=>{
  const $=s=>document.querySelector(s);
  const canReport=t=>Number(t.owner_id)===currentActorId()||['manager','pm'].includes(currentUsers.find(u=>Number(u.id)===currentActorId())?.role);
  window.routineTaskSummary=(t,actions=true)=>{
    if(t.task_type!=='ROUTINE')return '';
    const period=t.routine_week_start?`${t.routine_week_start}—${t.routine_week_end}`:'';
    const actual=t.routine_hours_updated_at||Number(t.actual_hours)>0?`${Number(t.actual_hours)}h`:'待填';
    return `<div class="routine-task-summary"><div class="muted">${period?`周期：${esc(period)} · `:''}标准 ${Number(t.estimated_hours)>0?`${Number(t.estimated_hours)}h`:'待配置'} / 实际 ${actual}</div>${actions&&canReport(t)?`<button type="button" class="secondary small" onclick="reportRoutineHours(${Number(t.id)})">填写本周工时</button>`:''}</div>`;
  };
  window.reportRoutineHours=async id=>{
    const task=currentTasks.find(t=>Number(t.id)===Number(id));if(!task||!canReport(task))return;
    const pending=requestActionInput({title:'填写本周实际工时',label:'累计实际工时（小时）',type:'number',
      hint:'填写本周累计投入，保存后替换原值，不重复累加；其他任务已记录的工时不要重复填入。',
      placeholder:'0—168 小时',value:task.routine_hours_updated_at||task.actual_hours?String(task.actual_hours):''});
    const input=$('#actionInputValue');input.min='0';input.max='168';input.step='0.5';
    const value=await pending;
    if(value===null)return;
    const actual=Number(value);
    if(!value.trim()||!Number.isFinite(actual)||actual<0||actual>168)return alert('实际工时须为 0—168 小时的有效数字');
    const r=await post(`/api/tasks/${id}/hours`,{actual_hours:actual,operator_id:currentActorId()}),out=await r.json().catch(()=>({}));
    if(!r.ok)return alert(out.error||'工时保存失败');
    await load();
    if($('#taskDetailDialog')?.open)showTaskDetails(id);
  };
  window.editRoutineTemplate=id=>{
    const t=(window.routineTemplates||[]).find(t=>Number(t.id)===Number(id));if(!t)return;
    $('#routineForm').reset();$('#routineId').value=t.id;$('#routineTitle').value=t.title;
    $('#routineNode').value=t.org_node_id;$('#routineOwner').value=t.owner_id;
    $('#routineDeliverable').value=t.deliverable;$('#routineHours').value=t.estimated_hours||'';
    $('#routineDialog h3').textContent='编辑例行任务模板';$('#routineDialog').showModal();$('#routineHours').focus();
  };
  window.addEventListener('load',()=>{
    const create=$('#orgRoutine>.org-panel-action button'),open=create?.onclick;
    if(create)create.onclick=()=>{open?.();$('#routineId').value='';$('#routineDialog h3').textContent='新增例行任务模板';$('#routineTitle').focus();};
    $('#routineForm').onsubmit=async e=>{
      e.preventDefault();const form=e.target,button=form.querySelector('button[type="submit"],button:not([type])');button.disabled=true;
      try{
        const id=$('#routineId').value,previous=(window.routineTemplates||[]).find(t=>Number(t.id)===Number(id));
        const r=await post('/api/routine/templates'+(id?'/'+id:''),{title:$('#routineTitle').value,org_node_id:Number($('#routineNode').value),
          owner_id:Number($('#routineOwner').value),deliverable:$('#routineDeliverable').value,estimated_hours:Number($('#routineHours').value),
          recurrence:'WEEKLY',actor_id:currentActorId(),priority:previous?.priority||'P1',description:previous?.description||''}),out=await r.json().catch(()=>({}));
        if(!r.ok)return alert(out.error||'模板保存失败');
        $('#routineDialog').close();form.reset();await loadOrg(true);
      }catch(error){alert(error.message||'模板保存失败');}finally{button.disabled=false;}
    };
    const card=window.taskCard;if(card)window.taskCard=t=>card(t).replace(/<\/div>$/,routineTaskSummary(t)+'</div>');
    const show=window.showTaskDetails;if(show)window.showTaskDetails=id=>{
      show(id);const t=currentTasks.find(t=>Number(t.id)===Number(id));if(t?.task_type!=='ROUTINE')return;
      const section=document.createElement('section');section.innerHTML=routineTaskSummary(t);$('#taskDetailContent').prepend(section);
    };
    const edit=window.editTask;if(edit)window.editTask=id=>{edit(id);setTimeout(()=>{
      const t=currentTasks.find(t=>Number(t.id)===Number(id)),input=$('#taskEditForm [name="estimated_hours"]');
      if(t?.routine_week_start&&input){input.readOnly=true;input.title='来自生成时的模板快照，修改模板仅影响后续任务';}
    },0)};
    const style=document.createElement('style');style.textContent='.routine-task-summary{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;margin-top:8px}.routine-task-summary .muted{font-size:12px}.routine-template-row{padding:14px 0;border-bottom:1px solid #e5eaf2}.routine-template-row:last-child{border-bottom:0}.routine-template-row button{margin-top:8px}.routine-task-summary>button,.routine-template-row>button{width:auto;flex:0 0 auto}';document.head.append(style);
    renderBoard();renderTaskList();
  });
})();

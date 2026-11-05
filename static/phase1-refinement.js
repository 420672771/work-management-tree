/* First-stage usability refinements.  Kept separate from the legacy inline page so
   the existing workflow and API contracts remain stable while the UI is simplified. */
(()=>{
  const $=selector=>document.querySelector(selector);
  const fieldFor=element=>element?.closest('.form-field')||element?.parentElement;

  function optionalLabel(field,text){
    if(!field)return;
    const label=field.querySelector('label');
    if(label)label.innerHTML=`${text}<span class="optional-note">（选填）</span>`;
    field.querySelector('input,textarea,select')?.removeAttribute('required');
  }

  function moveIntoAdvanced(form,fields){
    const existing=form.querySelector('.task-advanced-fields');
    const box=existing||document.createElement('details');
    if(!existing){
      box.className='task-advanced-fields full';
      box.innerHTML='<summary>更多信息（选填）</summary><div class="task-advanced-content"></div>';
      const actions=form.querySelector('.form-actions');
      actions?.before(box);
    }
    const content=box.querySelector('.task-advanced-content');
    fields.filter(Boolean).forEach(field=>content.append(field));
  }

  function compactCreateForm(){
    const form=$('#form');
    if(!form||form.dataset.phaseOneReady)return;
    form.dataset.phaseOneReady='1';
    const description=form.querySelector('[name="description"]');
    optionalLabel(fieldFor(description),'任务详情');
    const type=fieldFor(form.querySelector('[name="task_type"]'));
    const priority=fieldFor(form.querySelector('[name="priority"]'));
    const performance=$('#performanceTaskFields');
    if(performance){
      performance.querySelectorAll('[required]').forEach(input=>input.removeAttribute('required'));
      performance.querySelectorAll('label').forEach(label=>{
        if(!label.textContent.includes('选填'))label.insertAdjacentHTML('beforeend','<span class="optional-note">（选填）</span>');
      });
    }
    moveIntoAdvanced(form,[type,priority,performance,fieldFor(description)]);
    const node=fieldFor(form.querySelector('[name="org_node_id"]'));
    const owner=fieldFor(form.querySelector('[name="owner_id"]'));
    const due=fieldFor(form.querySelector('[name="due_at"]'));
    [node,owner,due].forEach(field=>field?.classList.add('core-create-field'));
  }

  function compactEditForm(){
    const form=$('#taskEditForm');
    if(!form||form.dataset.phaseOneReady)return;
    form.dataset.phaseOneReady='1';
    optionalLabel(fieldFor(form.querySelector('[name="description"]')),'任务详情');
    optionalLabel(fieldFor(form.querySelector('[name="deliverable"]')),'交付物 / 验收标准');
    const type=fieldFor(form.querySelector('[name="task_type"]'));
    const priority=fieldFor(form.querySelector('[name="priority"]'));
    const performance=form.querySelector('.performance-edit-fields');
    moveIntoAdvanced(form,[type,priority,performance,fieldFor(form.querySelector('[name="description"]')),fieldFor(form.querySelector('[name="deliverable"]'))]);
  }

  function refineTaskDialog(){
    const container=$('#taskDetailContent');
    if(!container)return;
    const observer=new MutationObserver(()=>setTimeout(compactEditForm,0));
    observer.observe(container,{childList:true,subtree:true});
  }

  function simplifyOrganisationNavigation(){
    const basicTab=$('#orgBasicTab');
    const basicPanel=$('#orgBasic');
    basicTab?.remove();
    basicPanel?.classList.add('hidden');
    const original=window.openOrgSection;
    if(typeof original==='function'&&!original.__phaseOneWrapped){
      const wrapped=async section=>original(section==='orgBasic'?'orgPosition':section);
      wrapped.__phaseOneWrapped=true;
      window.openOrgSection=wrapped;
    }
    if(typeof window.openPage==='function'){
      const originalOpenPage=window.openPage;
      window.openPage=page=>{
        originalOpenPage(page);
        if(page==='org')setTimeout(()=>window.openOrgSection?.('orgPosition'),0);
      };
    }
  }

  function simplifyHomeSummary(){
    const headerStats=document.querySelector('.workspace-header .stats');
    if(headerStats)headerStats.setAttribute('aria-hidden','true');
  }

  function installStyles(){
    const style=document.createElement('style');
    style.id='phase-one-refinement-style';
    style.textContent=`
      .workspace-header .stats{display:none!important}
      #homePage #form .optional-note,#taskEditForm .optional-note{margin-left:4px;color:#78869a;font-size:12px;font-weight:400}
      #homePage #form.task-create-form{grid-template-columns:repeat(6,minmax(0,1fr))!important}
      #homePage #form.task-create-form .core-create-field{grid-column:span 2!important}
      #homePage #form .task-advanced-fields,#taskEditForm .task-advanced-fields{grid-column:1/-1;margin-top:12px;border:1px solid #e1e8f2;border-radius:10px;background:#f8faff}
      #homePage #form .task-advanced-fields>summary,#taskEditForm .task-advanced-fields>summary{padding:10px 12px;cursor:pointer;color:#4a5b72;font-size:13px;font-weight:600;list-style:none}
      #homePage #form .task-advanced-fields>summary::-webkit-details-marker,#taskEditForm .task-advanced-fields>summary::-webkit-details-marker{display:none}
      #homePage #form .task-advanced-fields>summary:after,#taskEditForm .task-advanced-fields>summary:after{content:'+';float:right;color:#2b67d9;font-size:17px;line-height:14px}
      #homePage #form .task-advanced-fields[open]>summary,#taskEditForm .task-advanced-fields[open]>summary{border-bottom:1px solid #e4ebf4}
      #homePage #form .task-advanced-fields[open]>summary:after,#taskEditForm .task-advanced-fields[open]>summary:after{content:'−'}
      #homePage #form .task-advanced-content{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 12px;padding:0 12px 12px}
      #homePage #form .task-advanced-content>.form-field,#homePage #form .task-advanced-content>.full{grid-column:span 1!important}
      #homePage #form .task-advanced-content>.full{grid-column:1/-1!important}
      #homePage #form .task-advanced-content .task-filters{margin-top:10px}
      #taskEditForm .task-advanced-content{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 12px}
      #taskEditForm .task-advanced-content>.full{grid-column:1/-1!important}
      @media(max-width:800px){#homePage #form.task-create-form{grid-template-columns:1fr!important}#homePage #form.task-create-form .core-create-field{grid-column:1!important}#homePage #form .task-advanced-content,#taskEditForm .task-advanced-content{grid-template-columns:1fr!important}#homePage #form .task-advanced-content>.form-field,#homePage #form .task-advanced-content>.full,#taskEditForm .task-advanced-content>.full{grid-column:1!important}}
    `;
    document.head.append(style);
  }

  window.addEventListener('load',()=>{
    installStyles();
    compactCreateForm();
    refineTaskDialog();
    simplifyOrganisationNavigation();
    simplifyHomeSummary();
  });
})();

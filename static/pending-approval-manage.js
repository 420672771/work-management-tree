/* Pending task confirmations are drafts until their approver accepts them.
 * Give the accountable owner, department managers and PMs a way to correct or
 * remove a submitted draft without granting the approver edit permissions. */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const actorId = () => Number(window.currentActorId?.() || 0);
  const dateOnly = (value) => value ? String(value).slice(0, 10) : '';
  const notify = (message, kind = 'success') => {
    let node = $('#pendingDraftNotice');
    if (!node) {
      node = document.createElement('div');
      node.id = 'pendingDraftNotice';
      node.className = 'pending-draft-notice';
      document.body.append(node);
    }
    node.textContent = message;
    node.dataset.kind = kind;
    node.classList.add('show');
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => node.classList.remove('show'), 3600);
  };

  const installStyle = () => {
    const style = document.createElement('style');
    style.textContent = `
      .pending-draft-actions{display:inline-flex;gap:8px;margin-left:8px;vertical-align:middle}
      .pending-draft-actions .secondary{background:#fff;color:#2f6bdd;border:1px solid #bfd2f0}
      .pending-draft-actions .danger{background:#fff;color:#c33b3b;border:1px solid #f0c5c5}
      #pendingDraftDialog{width:min(680px,calc(100vw - 32px));max-height:calc(100vh - 32px);padding:0;border:0;border-radius:16px;box-shadow:0 20px 64px #172b4d42}
      #pendingDraftDialog::backdrop{background:#14213a73}
      #pendingDraftDialog form{max-height:calc(100vh - 32px);overflow:auto;padding:28px 30px}
      #pendingDraftDialog h3{margin:0 0 8px;color:#1d3456}.pending-draft-hint{margin:0 0 18px;color:#6b778c;font-size:14px}
      .pending-draft-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px}.pending-draft-grid .full{grid-column:1/-1}
      .pending-draft-grid label{display:grid;gap:7px;color:#40516b;font-weight:600}.pending-draft-grid input,.pending-draft-grid textarea,.pending-draft-grid select{box-sizing:border-box;width:100%;min-height:42px;padding:9px 11px;border:1px solid #d6e0ee;border-radius:9px;background:#fff;color:#233551;font:inherit}.pending-draft-grid textarea{min-height:92px;resize:vertical}
      .pending-draft-dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:22px}.pending-draft-dialog-actions button{margin:0}
      .pending-draft-error{margin:16px 0 0;color:#a52b2b}
      .pending-draft-notice{position:fixed;z-index:9999;left:50%;bottom:28px;transform:translate(-50%,20px);max-width:min(520px,calc(100vw - 32px));padding:12px 16px;border:1px solid #b9e2c3;border-radius:10px;background:#edf9f0;color:#176b36;box-shadow:0 12px 32px #172b4d2b;opacity:0;pointer-events:none;transition:.18s ease}.pending-draft-notice[data-kind=error]{border-color:#f0c5c5;background:#fff4f4;color:#a52b2b}.pending-draft-notice.show{opacity:1;transform:translate(-50%,0)}
      #approvalDetailDialog .panel-heading{position:relative;min-height:34px;padding:0 48px 0 0}#approvalDetailDialog .approval-close-icon{position:absolute;top:0;right:0;display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;margin:0;padding:0;border:1px solid #dce4ef;border-radius:9px;background:#f8fafc;color:#66758b;font-size:25px;font-weight:400;line-height:1;box-shadow:none;cursor:pointer;transition:background .15s,color .15s,border-color .15s}#approvalDetailDialog .approval-close-icon:hover{border-color:#b9cbe5;background:#eef4ff;color:#17459b}
      @media(max-width:640px){#pendingDraftDialog form{padding:22px 18px}.pending-draft-grid{grid-template-columns:1fr}.pending-draft-grid .full{grid-column:auto}.pending-draft-actions{margin:8px 0 0;display:flex}}
    `;
    document.head.append(style);
  };

  const canManage = (approval, users) => {
    const task = approval.task || {};
    const actor = actorId();
    const current = users.find(user => Number(user.id) === actor);
    return Number(task.owner_id) === actor || ['manager', 'pm'].includes(current?.role);
  };

  const closeDialog = () => {
    const dialog = $('#pendingDraftDialog');
    if (dialog?.open) dialog.close();
  };

  const normalizeApprovalClose = () => {
    const closeButton = $('#approvalDetailDialog .panel-heading button');
    if (!closeButton) return;
    closeButton.className = 'approval-close-icon';
    closeButton.textContent = '×';
    closeButton.setAttribute('aria-label', '关闭');
    closeButton.title = '关闭';
  };

  const deleteDraft = async (approval) => {
    const task = approval.task || {};
    const approved = await window.requestActionConfirmation?.({
      title: '删除待确认任务',
      message: '删除后，该待确认事项及其任务草稿都会移除，无法恢复。',
      itemText: task.title || '该任务草稿',
      confirmLabel: '删除',
      danger: true
    }) ?? window.confirm(`确定删除“${task.title || '该任务草稿'}”吗？`);
    if (!approved) return;
    const response = await fetch(`/api/tasks/${encodeURIComponent(task.id)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'delete', operator_id: actorId()})
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) return notify(result.error || '删除失败，请稍后重试。', 'error');
    closeDialog();
    await window.loadApprovals?.();
    notify('已删除待确认任务草稿。');
  };

  const editDraft = async (approval) => {
    const task = approval.task || {};
    const [users, tree] = await Promise.all([window.get('/api/users'), window.get('/api/tree')]);
    const domains = tree.filter(item => ['domain','subdomain'].includes(item.node_type) && !item.inactive_at);
    let dialog = $('#pendingDraftDialog');
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'pendingDraftDialog';
      document.body.append(dialog);
    }
    dialog.innerHTML = `<form method="dialog" id="pendingDraftForm"><h3>修改待确认任务</h3><p class="pending-draft-hint">修改后将保留待确认状态；如更换责任人，系统会同步调整确认人。</p><div class="pending-draft-grid"><label class="full">任务标题<input name="title" required maxlength="200"></label><label>责任人<select name="owner_id" required></select></label><label>责任域<select name="org_node_id" required></select></label><label>截止日期<input name="due_at" type="date" required></label><label>优先级<select name="priority"><option>P0</option><option>P1</option><option>P2</option></select></label><label class="full">任务详情<textarea name="description" placeholder="补充背景、工作范围或关键约束"></textarea></label></div><p class="pending-draft-error" role="alert" tabindex="-1" hidden></p><div class="pending-draft-dialog-actions"><button type="button" class="secondary" data-cancel>取消</button><button type="submit">保存修改</button></div></form>`;
    const form = $('#pendingDraftForm');
    form.title.value = task.title || '';
    form.description.value = task.description || '';
    form.due_at.value = dateOnly(task.due_at);
    form.priority.value = task.priority || 'P1';
    form.owner_id.innerHTML = users.map(user => `<option value="${Number(user.id)}">${String(user.name).replace(/[&<>"']/g, '')}</option>`).join('');
    form.org_node_id.innerHTML = domains.map(domain => `<option value="${Number(domain.id)}">${String(domain.name).replace(/[&<>"']/g, '')}</option>`).join('');
    form.owner_id.value = String(task.owner_id || '');
    form.org_node_id.value = String(task.org_node_id || '');
    form.querySelector('[data-cancel]').onclick = closeDialog;
    form.onsubmit = async (event) => {
      event.preventDefault();
      const submit = form.querySelector('[type=submit]');
      const errorMessage = form.querySelector('[role=alert]');
      errorMessage.hidden = true; errorMessage.textContent = '';
      submit.disabled = true; submit.textContent = '正在保存…';
      try {
        const values = new FormData(form);
        const body = {action: 'update', operator_id: actorId()};
        // Submit only edited fields. The simplified page removes legacy
        // deliverable controls; never read them or erase their stored values.
        // A domain-only correction must also retain the exact deadline and
        // leave the existing owner/confirmation chain alone.
        for (const name of ['title', 'description', 'owner_id', 'org_node_id', 'due_at', 'priority']) {
          const value = values.get(name);
          if (value === null) continue;
          const original = name === 'due_at' ? dateOnly(task[name]) : String(task[name] ?? '');
          if (value === original) continue;
          body[name] = ['owner_id', 'org_node_id'].includes(name) ? Number(value)
            : name === 'due_at' && value ? `${value}T17:00:00+08:00` : value.trim();
        }
        if (Object.keys(body).length === 2) { closeDialog(); return; }
        const response = await fetch(`/api/tasks/${encodeURIComponent(task.id)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw Error(result.error || '保存失败，请稍后重试。');
        closeDialog();
        await window.loadApprovals?.();
        notify('待确认任务已更新，等待确认人处理。');
      } catch (error) {
        errorMessage.textContent = error.message || '保存失败，请稍后重试。';
        errorMessage.hidden = false; errorMessage.focus();
      }
      finally { submit.disabled = false; submit.textContent = '保存修改'; }
    };
    if (!dialog.open) dialog.showModal();
  };

  const attachActions = async () => {
    const approvals = await window.get('/api/approvals');
    const users = await window.get('/api/users');
    const cards = [...document.querySelectorAll('#approvals .approval-card')];
    cards.forEach((card, index) => {
      const approval = approvals[index];
      if (!approval || approval.biz_type !== 'task_confirm' || !canManage(approval, users)) return;
      const actions = card.querySelector('.task-actions');
      if (!actions || actions.querySelector('.pending-draft-actions')) return;
      const wrap = document.createElement('span');
      wrap.className = 'pending-draft-actions';
      const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'secondary'; edit.textContent = '修改草稿';
      edit.onclick = event => { event.stopPropagation(); editDraft(approval); };
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'danger'; remove.textContent = '删除草稿';
      remove.onclick = event => { event.stopPropagation(); deleteDraft(approval); };
      wrap.append(edit, remove); actions.append(wrap);
    });
  };

  window.addEventListener('load', () => {
    installStyle();
    normalizeApprovalClose();
    const baseLoadApprovals = window.loadApprovals;
    window.loadApprovals = async (...args) => {
      const output = await baseLoadApprovals?.(...args);
      try { await attachActions(); } catch (error) { console.warn('待确认草稿管理按钮加载失败', error); }
      return output;
    };
    const baseOpenApprovalDetail = window.openApprovalDetail;
    window.openApprovalDetail = async (id) => {
      const output = await baseOpenApprovalDetail?.(id);
      normalizeApprovalClose();
      try {
        const [approval, users] = await Promise.all([window.get(`/api/approvals/${id}`), window.get('/api/users')]);
        if (approval.biz_type !== 'task_confirm' || !canManage(approval, users)) return output;
        const actions = $('#approvalDetailContent .approval-detail-actions');
        if (!actions || actions.querySelector('.pending-draft-actions')) return output;
        const wrap = document.createElement('span'); wrap.className = 'pending-draft-actions';
        const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'secondary'; edit.textContent = '修改草稿'; edit.onclick = () => editDraft(approval);
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'danger'; remove.textContent = '删除草稿'; remove.onclick = () => deleteDraft(approval);
        wrap.append(edit, remove); actions.prepend(wrap);
      } catch (_) { /* stale-approval guard handles removed items */ }
      return output;
    };
  });
})();

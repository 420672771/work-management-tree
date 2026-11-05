(() => {
  let toastTimer;
  const ensureStyle = () => {
    if (document.querySelector('#task-create-feedback-style')) return;
    const style = document.createElement('style');
    style.id = 'task-create-feedback-style';
    style.textContent = `.task-create-toast{position:fixed;top:24px;right:24px;z-index:12000;display:flex;gap:12px;align-items:flex-start;max-width:420px;padding:16px 18px;border:1px solid #b8e3c4;border-radius:14px;background:#fff;color:#173d2a;box-shadow:0 16px 42px rgba(15,49,83,.28);transform:translateY(-14px);opacity:0;pointer-events:none;transition:opacity .2s ease,transform .2s ease}.task-create-toast.is-visible{transform:translateY(0);opacity:1}.task-create-toast__icon{display:grid;place-items:center;flex:none;width:24px;height:24px;border-radius:50%;background:#1c9b58;color:#fff;font-weight:800;font-style:normal}.task-create-toast strong{display:block;font-size:16px}.task-create-toast span{display:block;margin-top:4px;color:#61776a;font-size:13px;line-height:1.45}.task-create-feedback{min-height:22px;margin:12px 0 0;padding:9px 12px;border-radius:8px;font-size:14px;font-weight:600}.task-create-feedback:not(:empty){background:#f3f7fc;color:#436078}.task-create-feedback.success{background:#edf9f1;color:#178049;border:1px solid #bde8c8}.task-create-feedback.error{background:#fff2f2;color:#c83333;border:1px solid #f0c1c1}@media(max-width:640px){.task-create-toast{top:14px;right:14px;left:14px;max-width:none}}`;
    document.head.append(style);
  };
  const showCreateToast = (title, detail, error = false) => {
    ensureStyle();
    let toast = document.querySelector('#taskCreateToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'taskCreateToast';
      toast.className = 'task-create-toast';
      toast.innerHTML = '<i class="task-create-toast__icon"></i><div><strong></strong><span></span></div>';
      document.body.append(toast);
    }
    const icon = toast.querySelector('.task-create-toast__icon');
    icon.textContent = error ? '!' : '✓';
    icon.style.background = error ? '#d84a4a' : '#1c9b58';
    toast.querySelector('strong').textContent = title;
    toast.querySelector('span').textContent = detail;
    requestAnimationFrame(() => toast.classList.add('is-visible'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 5000);
  };
  const installReliableCreateHandler = () => {
    const form = document.querySelector('#form');
    if (!form || form.dataset.createFeedbackBound === '1') return;
    form.dataset.createFeedbackBound = '1';
    // Use the capture phase.  The legacy page has more than one inline
    // onsubmit assignment; capturing here guarantees that every create action
    // goes through the same visible success/failure feedback.
    form.addEventListener('submit', async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const submit = event.submitter || form.querySelector('button[type="submit"], button');
      const originalText = submit?.textContent || '创建任务';
      const message = document.querySelector('#msg');
      const setMessage = (text, type = '') => {
        if (!message) return;
        message.textContent = text;
        message.className = `task-create-feedback ${type}`;
        message.setAttribute('role', 'status');
        message.setAttribute('aria-live', 'polite');
      };
      const data = Object.fromEntries(new FormData(form));
      try {
        const similar = await getSimilarTasks(data.title);
        if (similar.length && !confirm(`发现 ${similar.length} 条相同或相似任务，仍要创建吗？`)) return;
        if (submit) {
          submit.disabled = true;
          submit.textContent = '正在创建…';
          submit.setAttribute('aria-busy', 'true');
        }
        setMessage('正在创建任务，请稍候…');
        data.due_at = `${data.due_at}T17:00:00+08:00`;
        const response = await post('/api/tasks', data);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || '创建失败，请检查必填信息后重试');
        form.reset();
        window.clearSimilarTasks?.();
        const taskNo = payload.id ? `任务 #${payload.id} 已创建` : '任务已创建';
        setMessage(`${taskNo}，已提交直属上级确认。`, 'success');
        showCreateToast(taskNo, '任务已进入待确认；上级确认后才会出现在管理树和任务列表中。');
        // 待确认任务不会进入首页管理树或正式任务列表，因此不刷新首页，
        // 避免用户刚完成录入就看到页面跳动或管理树重绘。
      } catch (error) {
        const text = error?.message || '创建失败，请稍后重试';
        setMessage(text, 'error');
        showCreateToast('任务未创建成功', text, true);
      } finally {
        if (submit) {
          submit.disabled = false;
          submit.textContent = originalText;
          submit.removeAttribute('aria-busy');
        }
      }
    }, true);
  };

  // This script is injected at the end of the document, so the form already
  // exists.  Bind now instead of waiting for window.load, otherwise a slow
  // page load can leave the old, nearly invisible inline message in charge.
  installReliableCreateHandler();

  window.addEventListener('load', () => {
    // The two home columns are a CSS grid with stretch alignment.  Do not set
    // an inline height here: measuring while a form is submitting can briefly
    // return its collapsed height and squash the management tree.

    const form = document.querySelector('#form');
    if (!form) return;
    // The capture-phase handler above owns task creation.  Keep this legacy
    // assignment from replacing it when older inline modules finish loading.
    if (form.dataset.createFeedbackBound === '1') return;
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = event.submitter || form.querySelector('button[type="submit"], button');
      const originalText = submit?.textContent;
      const message = document.querySelector('#msg');
      const setMessage = (text, type) => {
        if (!message) return;
        message.textContent = text;
        message.className = `task-create-feedback ${type || ''}`;
      };
      const data = Object.fromEntries(new FormData(form));
      try {
        const similar = await getSimilarTasks(data.title);
        if (similar.length && !confirm(`发现 ${similar.length} 条相同或相似任务，仍要创建吗？`)) return;
        if (submit) { submit.disabled = true; submit.textContent = '正在创建…'; }
        setMessage('正在提交任务，请稍候…');
        data.due_at = `${data.due_at}T17:00:00+08:00`;
        const response = await post('/api/tasks', data);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || '创建失败，请检查填写内容后重试');
        form.reset();
        if (window.clearSimilarTasks) window.clearSimilarTasks();
        setMessage('创建成功：任务已提交，等待直属上级确认后生效。', 'success');
        showCreateToast('任务创建成功', '已提交上级确认；确认后会出现在管理树与任务列表。');
        await load();
      } catch (error) {
        const text = error.message || '创建失败，请稍后重试';
        setMessage(text, 'error');
        showCreateToast('任务未创建成功', text, true);
      } finally {
        if (submit) { submit.disabled = false; submit.textContent = originalText || '创建任务'; }
      }
    };
  });
})();

/* Keep a stale browser card from opening a broken confirmation drawer. */
window.addEventListener('load', () => {
  const baseOpenApprovalDetail = window.openApprovalDetail;
  if (!baseOpenApprovalDetail || baseOpenApprovalDetail.__staleApprovalGuard) return;

  const guardedOpenApprovalDetail = async id => {
    try {
      const actor=Number(window.currentActorId?.()||0);
      const response = await fetch('/api/approvals/' + encodeURIComponent(id)+(actor?'?actor_id='+actor:''), {cache: 'no-store'});
      if (response.status === 404) {
        const dialog = document.querySelector('#approvalDetailDialog');
        if (dialog?.open) dialog.close();
        await window.loadApprovals?.();
        alert('该确认事项已移除或当前无权查看，待确认列表已自动刷新。');
        return;
      }
    } catch (_) {
      // Let the original handler retain its normal network-error behavior.
    }
    return baseOpenApprovalDetail(id);
  };

  guardedOpenApprovalDetail.__staleApprovalGuard = true;
  window.openApprovalDetail = guardedOpenApprovalDetail;
});

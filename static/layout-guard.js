/*
 * Last-resort layout invariants.
 * Feature scripts are allowed to render their own content, but must never turn
 * shared navigation into full-width primary buttons or break the home two-column
 * layout.  This file is intentionally loaded after every feature script.
 */
(() => {
  const css = `
    /* Secondary navigation: one compact row, wrapping only on narrow screens. */
    .section-subnav,
    .org-subnav,
    .performance-tabs {
      display:flex !important;
      flex-direction:row !important;
      flex-wrap:wrap !important;
      align-items:center !important;
      justify-content:flex-start !important;
      gap:8px !important;
      width:100% !important;
      max-width:100% !important;
      min-width:0 !important;
      min-height:0 !important;
      margin:0 0 16px !important;
      padding:8px !important;
      box-sizing:border-box !important;
      background:#fff !important;
      border:1px solid #e3eaf5 !important;
      border-radius:12px !important;
      box-shadow:0 4px 16px rgba(26,54,93,.04) !important;
    }
    .section-subnav button,
    .org-subnav button,
    .performance-tabs button {
      display:inline-flex !important;
      flex:0 0 auto !important;
      align-items:center !important;
      justify-content:center !important;
      width:auto !important;
      min-width:0 !important;
      max-width:max-content !important;
      height:auto !important;
      margin:0 !important;
      padding:8px 14px !important;
      border:1px solid transparent !important;
      border-radius:8px !important;
      background:transparent !important;
      color:#52627a !important;
      box-shadow:none !important;
      white-space:nowrap !important;
      line-height:1.4 !important;
    }
    .section-subnav button.active,
    .org-subnav button.active,
    .performance-tabs button.active {
      background:#edf4ff !important;
      color:#2466cf !important;
      border-color:#dce9ff !important;
      font-weight:700 !important;
    }

    /* Performance filters are controls, not a navigation row.  Keep the
       query action compact even when a generic button rule is injected. */
    .performance-filters {
      display:grid !important;
      grid-template-columns:minmax(200px,240px) minmax(180px,240px) 104px !important;
      align-items:end !important;
      justify-content:start !important;
      gap:12px !important;
      width:100% !important;
      margin:0 0 18px !important;
    }
    .performance-filters > div {
      min-width:0 !important;
    }
    .performance-filters > div > label {
      display:block !important;
      margin:0 0 6px !important;
    }
    .performance-filters > div > input,
    .performance-filters > div > select {
      box-sizing:border-box !important;
      width:100% !important;
      min-width:0 !important;
    }
    .performance-filters > button {
      display:inline-flex !important;
      grid-column:3 !important;
      justify-self:start !important;
      flex:0 0 104px !important;
      align-items:center !important;
      justify-content:center !important;
      align-self:end !important;
      width:104px !important;
      min-width:104px !important;
      max-width:104px !important;
      height:42px !important;
      margin:0 !important;
      padding:9px 14px !important;
    }

    /* Home page: the left tree panel must end level with the operations panel. */
    #homePage > .grid {
      align-items:start !important;
    }
    #homePage > .grid > .card:first-child:not(.tree-fullscreen) {
      display:flex !important;
      flex-direction:column !important;
      align-self:start !important;
      min-height:0 !important;
      overflow:hidden !important;
      box-sizing:border-box !important;
    }
    #homePage > .grid > .card:first-child:not(.tree-fullscreen) .tree {
      flex:0 0 auto !important;
      min-height:0 !important;
      overflow-y:auto !important;
      overflow-x:hidden !important;
      overscroll-behavior:contain !important;
      scrollbar-gutter:stable !important;
      padding-right:8px !important;
    }
    #homePage > .grid > .card:first-child:not(.tree-fullscreen) .tree::-webkit-scrollbar { width:9px !important; }
    #homePage > .grid > .card:first-child:not(.tree-fullscreen) .tree::-webkit-scrollbar-thumb { background:#9aa8ba !important; border-radius:10px !important; }

    @media (max-width:800px) {
      .section-subnav,
      .org-subnav,
      .performance-tabs { gap:6px !important; }
      .section-subnav button,
      .org-subnav button,
      .performance-tabs button { padding:7px 10px !important; }
      .performance-filters {
        grid-template-columns:1fr !important;
      }
      .performance-filters > button {
        width:100% !important;
        min-width:0 !important;
        max-width:none !important;
      }
    }
  `;

  const style = document.createElement('style');
  style.id = 'wmt-layout-guard';
  style.textContent = css;
  document.head.appendChild(style);

  function setImportant(node, key, value) {
    node.style.setProperty(key, value, 'important');
  }

  function normalizeSecondaryControls() {
    const compact = window.matchMedia('(max-width:800px)').matches;
    document.querySelectorAll('.section-subnav, .org-subnav, .performance-tabs').forEach(nav => {
      ['display', 'flex-direction', 'flex-wrap', 'align-items', 'justify-content', 'width', 'max-width'].forEach((key, index) => {
        const values = ['flex', 'row', 'wrap', 'center', 'flex-start', '100%', '100%'];
        setImportant(nav, key, values[index]);
      });
      nav.querySelectorAll('button').forEach(button => {
        setImportant(button, 'display', 'inline-flex');
        setImportant(button, 'flex', '0 0 auto');
        setImportant(button, 'width', 'auto');
        setImportant(button, 'min-width', '0');
        setImportant(button, 'max-width', 'max-content');
        setImportant(button, 'margin', '0');
      });
    });
    document.querySelectorAll('.performance-filters').forEach(filters => {
      setImportant(filters, 'display', 'grid');
      setImportant(filters, 'grid-template-columns', compact ? '1fr' : 'minmax(200px,240px) minmax(180px,240px) 104px');
      setImportant(filters, 'align-items', 'end');
      setImportant(filters, 'gap', '12px');
      const action = filters.querySelector('button');
      if (action) {
        const width = compact ? '100%' : '104px';
        [['display', 'inline-flex'], ['grid-column', compact ? 'auto' : '3'], ['justify-self', compact ? 'stretch' : 'start'], ['width', width], ['min-width', compact ? '0' : '104px'], ['max-width', compact ? 'none' : '104px'], ['height', '42px'], ['margin', '0'], ['flex', compact ? '0 0 auto' : '0 0 104px']].forEach(([key, value]) => setImportant(action, key, value));
      }
    });
  }

  function syncManagementTreeHeight() {
    const treeCard = document.querySelector('#homePage > .grid > .card:first-child');
    const operations = document.querySelector('#homePage > .grid > .home-operations');
    if (!treeCard || !operations || treeCard.classList.contains('tree-fullscreen')) return;
    const tree = treeCard.querySelector('.tree');
    if (!tree) return;

    if (window.matchMedia('(max-width: 800px)').matches) {
      ['height', 'min-height', 'max-height'].forEach(k => treeCard.style.removeProperty(k));
      ['height', 'min-height', 'max-height', 'flex'].forEach(k => tree.style.removeProperty(k));
      tree.style.setProperty('overflow-y', 'visible', 'important');
      return;
    }

    const targetHeight = Math.ceil(operations.getBoundingClientRect().height);
    if (targetHeight < 180) return;
    const cardStyle = getComputedStyle(treeCard);
    const padding = (parseFloat(cardStyle.paddingTop) || 0) + (parseFloat(cardStyle.paddingBottom) || 0);
    const heading = treeCard.querySelector('.panel-heading');
    const headingHeight = heading ? Math.ceil(heading.getBoundingClientRect().height) : 0;
    const treeHeight = Math.max(150, targetHeight - padding - headingHeight - 4);

    ['height', 'min-height', 'max-height'].forEach(k => treeCard.style.setProperty(k, `${targetHeight}px`, 'important'));
    tree.style.setProperty('height', `${treeHeight}px`, 'important');
    tree.style.setProperty('min-height', '0', 'important');
    tree.style.setProperty('max-height', `${treeHeight}px`, 'important');
    tree.style.setProperty('flex', `0 0 ${treeHeight}px`, 'important');
    tree.style.setProperty('overflow-y', 'auto', 'important');
    tree.style.setProperty('overflow-x', 'hidden', 'important');
  }

  window.syncManagementTreeHeight = syncManagementTreeHeight;
  const queueSync = () => requestAnimationFrame(syncManagementTreeHeight);
  window.addEventListener('load', () => {
    normalizeSecondaryControls();
    queueSync();
    setTimeout(queueSync, 80);
    setTimeout(queueSync, 350);
    const operations = document.querySelector('#homePage > .grid > .home-operations');
    const tree = document.querySelector('#homePage > .grid > .card:first-child .tree');
    if (window.ResizeObserver && operations) new ResizeObserver(queueSync).observe(operations);
    if (window.MutationObserver && tree) new MutationObserver(queueSync).observe(tree, {childList:true, subtree:true});
  });
  window.addEventListener('resize', () => {
    normalizeSecondaryControls();
    queueSync();
  });

  /* Keep this guard after any feature style injected during page switches. */
  if (window.MutationObserver) new MutationObserver(records => {
    if (records.some(r => [...r.addedNodes].some(n => n.nodeType === 1 && n !== style && n.tagName === 'STYLE'))) {
      requestAnimationFrame(() => document.head.appendChild(style));
    }
  }).observe(document.head, {childList:true});

  if (window.MutationObserver) new MutationObserver(records => {
    if (records.some(record => record.addedNodes.length || record.type === 'attributes')) {
      requestAnimationFrame(() => normalizeSecondaryControls());
    }
  }).observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['class']});
})();

(() => {
  'use strict';

  const DB_NAME = 'jingxiao-ipad-library';
  const DB_VERSION = 1;
  const STORE = 'books';
  const MAX_FILE_BYTES = 25 * 1024 * 1024;
  const MAX_BACKUP_BYTES = 100 * 1024 * 1024;
  const PAGE_LINES = 140;
  const state = { books: [], activeId: null, mode: 'shelf', query: '', page: 0, fontSize: 20 };
  const app = document.getElementById('app');
  const layout = document.getElementById('layout');
  const videoView = document.getElementById('videoView');
  const workspace = document.getElementById('workspace');
  let dbPromise;
  let toastTimer;

  function database() {
    if (!dbPromise) dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: 'id' });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('无法打开本机书库'));
      request.onblocked = () => reject(new Error('本机书库正在被另一个页面占用，请关闭旧页面后重试'));
    });
    return dbPromise;
  }

  async function storeRequest(mode, action, value) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, mode);
      const request = value === undefined ? transaction.objectStore(STORE)[action]() : transaction.objectStore(STORE)[action](value);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('本机书库操作失败'));
      transaction.onabort = () => reject(transaction.error || new Error('本机书库写入中断'));
    });
  }

  const allBooks = () => storeRequest('readonly', 'getAll');
  const saveBook = book => storeRequest('readwrite', 'put', book);
  const removeBook = id => storeRequest('readwrite', 'delete', id);
  const activeBook = () => state.books.find(book => book.id === state.activeId);

  function escapeHtml(value = '') {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
  }

  function dateLabel(timestamp) {
    return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(new Date(timestamp));
  }

  function id() {
    return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  async function refreshBooks() {
    state.books = (await allBooks()).sort((a, b) => b.updatedAt - a.updatedAt);
    if (state.activeId && !activeBook()) state.activeId = null;
    if (activeBook()) state.page = Math.max(0, Number(activeBook().progressPage) || 0);
    render();
  }

  function filteredBooks() {
    const query = state.query.trim().toLocaleLowerCase();
    if (!query) return state.books;
    return state.books.filter(book => book.title.toLocaleLowerCase().includes(query) || book.text.toLocaleLowerCase().includes(query));
  }

  function renderBookList() {
    const list = document.getElementById('bookList');
    if (!list) return;
    const books = filteredBooks();
    document.getElementById('bookCount').textContent = `${state.books.length} 本`;
    if (!books.length) {
      list.innerHTML = `<div class="empty-shelf">${state.query ? '没有找到匹配内容。' : '书架还是空的，导入一本 TXT 就能离线阅读。'}</div>`;
      return;
    }
    const query = state.query.trim();
    list.innerHTML = books.map(book => {
      const at = query ? book.text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
      const start = at >= 0 ? Math.max(0, at - 24) : 0;
      const snippet = book.text.slice(start, start + 90).replace(/\s+/g, ' ').trim();
      const progress = book.progressUpdatedAt > 0
        ? `上次读到第 ${(Number(book.progressPage) || 0) + 1} 页` : '尚未开始阅读';
      return `<button class="book-card${book.id === state.activeId ? ' active' : ''}" data-action="open" data-id="${escapeHtml(book.id)}"><strong>${escapeHtml(book.title)}</strong><small>${dateLabel(book.updatedAt)} 更新 · 约 ${book.text.length.toLocaleString()} 字 · ${progress}</small>${snippet ? `<span class="snippet">${escapeHtml(snippet)}</span>` : ''}</button>`;
    }).join('');
  }

  async function persistReadingProgress(book) {
    if (!book) return;
    const updated = { ...book, progressPage: state.page, progressUpdatedAt: Date.now() };
    await saveBook(updated);
    const index = state.books.findIndex(item => item.id === updated.id);
    if (index >= 0) state.books[index] = updated;
    renderBookList();
  }

  function render() {
    const book = activeBook();
    layout.classList.toggle('is-reading', !!book);
    renderBookList();
    renderWorkspace(book);
  }

  function renderWorkspace(book) {
    if (!book) {
      workspace.innerHTML = `<div class="welcome"><div class="welcome-mark" aria-hidden="true">校</div><h2>书和数据，都留在 iPad 本机</h2><p>导入 TXT 后即可离线阅读、全文检索、手工修订并导出。没有电脑服务，也不会把书稿上传到网络。</p><button class="primary-button" data-action="import">＋ 选择本地 TXT</button><p class="privacy-note">建议定期导出备份；清除 Safari 网站数据可能删除本机书库。</p></div>`;
      return;
    }
    if (state.mode === 'edit') {
      workspace.innerHTML = `<div class="work-head"><div class="work-title"><h2>${escapeHtml(book.title)}</h2><small>本机手工精校 · 保存后自动保留在书架</small></div><div class="work-actions"><button class="secondary-button" data-action="shelf">书架</button><button class="secondary-button" data-action="back-reader">返回阅读</button></div></div><div class="editor"><div class="replace-tools"><input id="findText" type="search" placeholder="查找文字"><input id="replaceText" type="text" placeholder="替换为"><button class="secondary-button" data-action="replace">替换全部</button></div><textarea id="editorText" aria-label="编辑书籍正文" spellcheck="false"></textarea><div class="editor-footer"><span>仅保存到本机。需要跨设备时请先导出文件。</span><div><button class="secondary-button" data-action="cancel-edit">取消</button><button class="primary-button" data-action="save-edit">保存修订</button></div></div></div>`;
      document.getElementById('editorText').value = book.text;
      return;
    }
    const lines = book.text.split(/\r?\n/).flatMap(line => {
      if (line.length <= 3000) return [line];
      const chunks = [];
      for (let offset = 0; offset < line.length; offset += 3000) chunks.push(line.slice(offset, offset + 3000));
      return chunks;
    });
    const pageCount = Math.max(1, Math.ceil(lines.length / PAGE_LINES));
    state.page = Math.min(state.page, pageCount - 1);
    const pageLines = lines.slice(state.page * PAGE_LINES, (state.page + 1) * PAGE_LINES);
    const content = pageLines.map(line => line ? `<p>${escapeHtml(line)}</p>` : '<p class="blank-line" aria-hidden="true"></p>').join('');
    workspace.innerHTML = `<div class="work-head"><div class="work-title"><h2>${escapeHtml(book.title)}</h2><small>最近保存 ${dateLabel(book.updatedAt)} · 保存在本机</small></div><div class="work-actions"><button class="secondary-button" data-action="shelf">书架</button><button class="secondary-button" data-action="edit">✎ 编辑</button><button class="secondary-button" data-action="export">导出 TXT</button><button class="danger-button" data-action="delete">删除</button></div></div><div class="reader-tools"><span>第 ${state.page + 1} / ${pageCount} 页</span><div><button data-action="font-down" aria-label="缩小字号">A−</button> <button data-action="font-up" aria-label="放大字号">A＋</button></div></div><article class="reader-body" style="font-size:${state.fontSize}px">${content || '<p>这本书暂时没有正文。</p>'}</article><nav class="pager" aria-label="阅读翻页"><button data-action="prev" ${state.page === 0 ? 'disabled' : ''}>上一页</button><span>${state.page + 1} / ${pageCount}</span><button data-action="next" ${state.page + 1 >= pageCount ? 'disabled' : ''}>下一页</button></nav>`;
    workspace.scrollTop = 0;
  }

  function decodeText(buffer) {
    const bytes = new Uint8Array(buffer);
    for (const encoding of ['utf-8', 'gb18030']) {
      try { return new TextDecoder(encoding, { fatal: true }).decode(bytes).replace(/^\uFEFF/, ''); } catch {}
    }
    return new TextDecoder('utf-8').decode(bytes).replace(/^\uFEFF/, '');
  }

  async function importFiles(files) {
    for (const file of files) {
      if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} 超过 25 MiB，请先拆分文件`);
      if (!/\.(txt|md)$/i.test(file.name)) throw new Error(`暂不支持 ${file.name}，请导入 TXT 或 Markdown`);
      const text = decodeText(await file.arrayBuffer());
      const now = Date.now();
      const title = file.name.replace(/\.(txt|md)$/i, '').trim() || '未命名书籍';
      await saveBook({ id: id(), title, filename: file.name, text, createdAt: now, updatedAt: now });
    }
    await refreshBooks();
    showToast(`已导入 ${files.length} 本，内容保存在本机`);
  }

  function exportBook(book) {
    const filename = `${book.title || '书籍'}-本机导出.txt`.replace(/[\\/:*?"<>|]/g, '_');
    const file = new File([book.text], filename, { type: 'text/plain;charset=utf-8' });
    if (navigator.canShare && navigator.canShare({ files: [file] }) && navigator.share) {
      navigator.share({ files: [file], title: book.title }).then(() => showToast('已打开系统分享，可存入“文件”')).catch(error => {
        if (error.name !== 'AbortError') showToast('分享失败，请重试');
      });
      return;
    }
    const blob = new Blob([book.text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    showToast('已生成 TXT 导出文件');
  }

  async function exportBackup() {
    const payload = window.iPadBackup.createBackup(await allBooks());
    const date = new Date().toISOString().slice(0, 10);
    const filename = `jingxiao-ipad-backup-${date}.json`;
    const file = new File([payload], filename, { type: 'application/json;charset=utf-8' });
    if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: '精校工作台本机备份' });
      showToast('已打开分享面板，可存入“文件”或传到另一台设备');
      return;
    }
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    showToast('备份已生成；文件只保存在你选择的位置');
  }

  async function restoreBackup(file) {
    if (file.size > MAX_BACKUP_BYTES) throw new Error('备份超过 100 MiB，请拆分后再导入');
    const incoming = window.iPadBackup.parseBackup(await file.text());
    if (!window.confirm(`备份含 ${incoming.length} 本书。将合并到本机书架，不覆盖不同正文；继续吗？`)) return;
    const current = await allBooks();
    const merged = window.iPadBackup.mergeBooks(current, incoming, id);
    for (const book of merged.books) await saveBook(book);
    await refreshBooks();
    showToast(`导入完成：新增 ${merged.added} 本，恢复 ${merged.progressUpdated} 条阅读进度${merged.idConflicts ? `，另存编号冲突 ${merged.idConflicts} 本` : ''}`);
  }

  function confirmDiscardEdit() {
    if (state.mode !== 'edit') return true;
    const editor = document.getElementById('editorText');
    const book = activeBook();
    return !editor || !book || editor.value === book.text || window.confirm('放弃尚未保存的修订吗？');
  }

  async function act(action, button) {
    const book = activeBook();
    switch (action) {
      case 'import': document.getElementById('fileInput').click(); break;
      case 'backup': await exportBackup(); break;
      case 'restore-backup': document.getElementById('backupInput').click(); break;
      case 'video':
        if (!confirmDiscardEdit()) break;
        layout.hidden = true;
        videoView.hidden = false;
        break;
      case 'library':
        videoView.hidden = true;
        layout.hidden = false;
        break;
      case 'open':
        if (!confirmDiscardEdit()) break;
        state.activeId = button.dataset.id;
        state.mode = 'reader';
        state.page = Math.max(0, Number(activeBook()?.progressPage) || 0);
        try { localStorage.setItem('ipad-active-book', state.activeId); } catch {}
        render();
        await persistReadingProgress(activeBook());
        break;
      case 'shelf':
        if (!confirmDiscardEdit()) break;
        state.activeId = null; state.mode = 'shelf'; render();
        try { localStorage.removeItem('ipad-active-book'); } catch {}
        break;
      case 'theme': {
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('ipad-theme', next); } catch {}
        break;
      }
      case 'edit': state.mode = 'edit'; renderWorkspace(book); break;
      case 'back-reader':
      case 'cancel-edit':
        if (!confirmDiscardEdit()) break;
        state.mode = 'reader'; renderWorkspace(book);
        break;
      case 'save-edit': {
        const text = document.getElementById('editorText').value;
        await saveBook({ ...book, text, updatedAt: Date.now() });
        state.mode = 'reader';
        await refreshBooks();
        showToast('修订已保存在本机');
        break;
      }
      case 'replace': {
        const find = document.getElementById('findText').value;
        if (!find) return showToast('先输入要查找的文字');
        const editor = document.getElementById('editorText');
        const escaped = find.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const matches = editor.value.match(new RegExp(escaped, 'g')) || [];
        editor.value = editor.value.replace(new RegExp(escaped, 'g'), document.getElementById('replaceText').value);
        showToast(`已替换 ${matches.length} 处`);
        break;
      }
      case 'prev':
        state.page = Math.max(0, state.page - 1);
        await persistReadingProgress(book);
        renderWorkspace(activeBook() || book);
        break;
      case 'next':
        state.page += 1;
        await persistReadingProgress(book);
        renderWorkspace(activeBook() || book);
        break;
      case 'font-down': state.fontSize = Math.max(15, state.fontSize - 1); renderWorkspace(book); break;
      case 'font-up': state.fontSize = Math.min(30, state.fontSize + 1); renderWorkspace(book); break;
      case 'export': if (book) exportBook(book); break;
      case 'delete':
        if (book && window.confirm(`从这台 iPad 删除“${book.title}”？此操作不能撤销。`)) {
          await removeBook(book.id);
          state.activeId = null;
          state.mode = 'shelf';
          try { localStorage.removeItem('ipad-active-book'); } catch {}
          await refreshBooks();
          showToast('已从本机书架删除');
        }
        break;
    }
  }

  app.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    act(button.dataset.action, button).catch(error => showToast(error.message || '操作失败'));
  });

  document.getElementById('searchInput').addEventListener('input', event => {
    state.query = event.target.value;
    renderBookList();
  });

  document.getElementById('fileInput').addEventListener('change', event => {
    const files = [...event.target.files];
    event.target.value = '';
    if (files.length) importFiles(files).catch(error => showToast(error.message || '导入失败'));
  });

  document.getElementById('backupInput').addEventListener('change', event => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (file) restoreBackup(file).catch(error => showToast(error.message || '备份导入失败'));
  });

  async function start() {
    let theme = null;
    try { theme = localStorage.getItem('ipad-theme'); } catch {}
    if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme;
    try {
      await database();
      state.books = (await allBooks()).sort((a, b) => b.updatedAt - a.updatedAt);
      let rememberedId = null;
      try { rememberedId = localStorage.getItem('ipad-active-book'); } catch {}
      const rememberedBook = state.books.find(book => book.id === rememberedId);
      if (rememberedBook) {
        state.activeId = rememberedBook.id;
        state.mode = 'reader';
        state.page = Math.max(0, Number(rememberedBook.progressPage) || 0);
      } else if (rememberedId) {
        try { localStorage.removeItem('ipad-active-book'); } catch {}
      }
      render();
      if (rememberedBook) await persistReadingProgress(activeBook());
      if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    } catch (error) {
      workspace.innerHTML = `<div class="welcome"><h2>本机书库暂不可用</h2><p>${escapeHtml(error.message)}<br>请在 Safari 的 HTTPS 页面中打开后重试。</p></div>`;
      showToast('浏览器未提供可用的本机存储');
    }
    if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
      navigator.serviceWorker.register('./sw.js').then(() => {
        document.getElementById('storageState').textContent = '本机离线可用';
      }).catch(() => {});
    }
  }

  start();
})();

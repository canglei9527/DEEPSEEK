(function (root) {
  'use strict';

  const FORMAT = 'jingxiao-ipad-backup';
  const VERSION = 1;
  const MAX_BOOKS = 2000;
  const MAX_TEXT_BYTES = 25 * 1024 * 1024;

  function normalizeBook(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('备份中有无效书籍记录');
    const id = String(value.id || '').trim();
    const title = String(value.title || '').trim();
    const text = value.text;
    if (!id || id.length > 160 || !title || title.length > 500 || typeof text !== 'string') {
      throw new Error('备份中有缺少书名、编号或正文的记录');
    }
    if (new TextEncoder().encode(text).byteLength > MAX_TEXT_BYTES) throw new Error(`《${title}》超过 25 MiB，无法导入`);
    const integer = (input, fallback = 0) => {
      const number = Number(input);
      return Number.isSafeInteger(number) && number >= 0 ? number : fallback;
    };
    return {
      id,
      title,
      filename: String(value.filename || `${title}.txt`).slice(0, 1000),
      text,
      createdAt: integer(value.createdAt, Date.now()),
      updatedAt: integer(value.updatedAt, Date.now()),
      progressPage: integer(value.progressPage),
      progressUpdatedAt: integer(value.progressUpdatedAt),
    };
  }

  function createBackup(books, exportedAt = Date.now()) {
    if (!Array.isArray(books) || books.length > MAX_BOOKS) throw new Error('书籍数量超出备份范围');
    return JSON.stringify({
      format: FORMAT,
      version: VERSION,
      exportedAt: Number.isSafeInteger(exportedAt) && exportedAt >= 0 ? exportedAt : Date.now(),
      books: books.map(normalizeBook),
    }, null, 2);
  }

  function parseBackup(text) {
    let value;
    try { value = JSON.parse(text); } catch (_) { throw new Error('备份文件不是有效的 JSON'); }
    if (!value || value.format !== FORMAT || value.version !== VERSION || !Array.isArray(value.books)) {
      throw new Error('不支持此备份格式或版本');
    }
    if (value.books.length > MAX_BOOKS) throw new Error('备份中的书籍数量过多');
    return value.books.map(normalizeBook);
  }

  function mergeBooks(existing, incoming, makeId) {
    if (!Array.isArray(existing) || !Array.isArray(incoming) || typeof makeId !== 'function') {
      throw new Error('无法合并备份数据');
    }
    const books = existing.map(normalizeBook);
    let added = 0;
    let progressUpdated = 0;
    let idConflicts = 0;

    incoming.map(normalizeBook).forEach(candidate => {
      const sameContent = books.find(book => book.title === candidate.title && book.text === candidate.text);
      if (sameContent) {
        if (candidate.progressUpdatedAt > sameContent.progressUpdatedAt) {
          sameContent.progressPage = candidate.progressPage;
          sameContent.progressUpdatedAt = candidate.progressUpdatedAt;
          progressUpdated += 1;
        }
        return;
      }
      if (books.some(book => book.id === candidate.id)) {
        idConflicts += 1;
        candidate.id = makeId();
        while (books.some(book => book.id === candidate.id)) candidate.id = makeId();
      }
      books.push(candidate);
      added += 1;
    });
    return { books, added, progressUpdated, idConflicts };
  }

  const api = { FORMAT, VERSION, createBackup, parseBackup, mergeBooks };
  root.iPadBackup = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));

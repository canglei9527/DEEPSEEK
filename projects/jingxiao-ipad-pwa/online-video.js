(function (root) {
  'use strict';

  const STORAGE_KEY = 'ipad-video-sources-v1';
  const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
  const REQUEST_TIMEOUT_MS = 15000;

  function secureUrl(value) {
    let url;
    try { url = new URL(String(value || '').trim()); } catch (_) { throw new Error('源地址不是有效网址'); }
    if (url.protocol !== 'https:' || url.username || url.password) {
      throw new Error('为避免 iPad 安全页拦截，请填写不带账号密码的 HTTPS 源地址');
    }
    url.hash = '';
    return url;
  }

  function createSource(name, url) {
    const title = String(name || '').trim();
    if (!title || title.length > 80) throw new Error('源名称不能为空，且不能超过 80 个字符');
    return { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, name: title, url: secureUrl(url).href };
  }

  function buildApiUrl(base, action, value, page = 1) {
    const url = secureUrl(base);
    url.searchParams.set('ac', action);
    if (action === 'list') {
      url.searchParams.set('wd', String(value || '').trim());
      url.searchParams.set('pg', String(Math.max(1, Number(page) || 1)));
    } else {
      url.searchParams.set('ids', String(value || '').trim());
    }
    return url.href;
  }

  function recordsFrom(payload) {
    if (!payload || typeof payload !== 'object') return [];
    const data = payload.data;
    const lists = [payload.list, data && data.list, data && data.vod, payload.results, Array.isArray(data) ? data : null];
    const list = lists.find(Array.isArray) || [];
    return list.filter(item => item && typeof item === 'object' && !Array.isArray(item)).map(item => ({
      id: String(item.vod_id ?? item.id ?? item.video_id ?? ''),
      title: String(item.vod_name ?? item.title ?? item.name ?? '未命名作品'),
      poster: String(item.vod_pic ?? item.pic ?? item.cover ?? ''),
      remarks: String(item.vod_remarks ?? item.remarks ?? item.update ?? ''),
      playFrom: item.vod_play_from ?? item.play_from ?? '',
      playUrl: item.vod_play_url ?? item.play_url ?? item.playUrl ?? item.episodes ?? '',
      raw: item,
    }));
  }

  function episodeGroups(record, baseUrl) {
    const raw = record.playUrl;
    if (Array.isArray(raw)) {
      const episodes = raw.map((item, index) => {
        if (typeof item === 'string') return parseEpisode(item, index, baseUrl);
        return {
          name: String(item.name || item.title || `第 ${index + 1} 集`),
          url: safeEpisodeUrl(item.url || item.play_url || '', baseUrl),
        };
      }).filter(item => item.url);
      return episodes.length ? [{ name: '默认线路', episodes }] : [];
    }
    const urls = String(raw || '').split('$$$');
    const names = String(record.playFrom || '').split('$$$');
    return urls.map((line, lineIndex) => ({
      name: names[lineIndex] || `线路 ${lineIndex + 1}`,
      episodes: line.split('#').map((item, episodeIndex) => parseEpisode(item, episodeIndex, baseUrl)).filter(item => item.url),
    })).filter(group => group.episodes.length);
  }

  function parseEpisode(raw, index, baseUrl) {
    const parts = String(raw || '').split('$');
    const value = parts.length > 1 ? parts.slice(1).join('$').trim() : parts[0].trim();
    let url = '';
    url = safeEpisodeUrl(value, baseUrl);
    return { name: parts.length > 1 ? parts[0].trim() || `第 ${index + 1} 集` : `第 ${index + 1} 集`, url };
  }

  function safeEpisodeUrl(value, baseUrl) {
    const candidate = String(value || '').trim();
    if (!candidate) return '';
    try { return secureUrl(new URL(candidate, baseUrl).href).href; } catch (_) { return ''; }
  }

  function isDirectMedia(value) {
    try {
      const url = secureUrl(value);
      return /\.(?:m3u8|mp4|m4v|mov|webm)(?:$|[?#])/i.test(url.href);
    } catch (_) { return false; }
  }

  async function fetchPayload(url) {
    let response;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      try { response = await fetch(url, { mode: 'cors', credentials: 'omit', redirect: 'follow', signal: controller.signal }); }
      catch (error) {
        if (error && error.name === 'AbortError') throw new Error('源站请求超过 15 秒，已停止；请检查网络或更换源');
        throw new Error('iPad 直连失败：源站可能禁止浏览器跨域，或当前网络无法访问；本机模式不会使用代理');
      }
      if (!response.ok) throw new Error(`源站返回 HTTP ${response.status}`);
      const length = Number(response.headers.get('content-length') || 0);
      if (length > MAX_RESPONSE_BYTES) throw new Error('源站响应超过 8 MiB，已停止读取');

      let text;
      if (response.body && response.body.getReader) {
        const reader = response.body.getReader();
        const chunks = [];
        let size = 0;
        try {
          while (true) {
            const part = await reader.read();
            if (part.done) break;
            size += part.value.byteLength;
            if (size > MAX_RESPONSE_BYTES) {
              await reader.cancel();
              throw new Error('源站响应超过 8 MiB，已停止读取');
            }
            chunks.push(part.value);
          }
        } finally { reader.releaseLock(); }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        text = new TextDecoder('utf-8', { fatal: false }).decode(bytes);
      } else {
        text = await response.text();
        if (new TextEncoder().encode(text).byteLength > MAX_RESPONSE_BYTES) throw new Error('源站响应超过 8 MiB，已停止读取');
      }
      try { return JSON.parse(text); } catch (_) { throw new Error('源站没有返回可识别的 JSON 接口数据'); }
    } catch (error) {
      if (error && error.name === 'AbortError') throw new Error('源站响应超过 15 秒，已停止；请检查网络或更换源');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function api(rootElement) {
    const sourceSelect = rootElement.querySelector('#videoSourceSelect');
    const sourceList = rootElement.querySelector('#videoSourceList');
    const status = rootElement.querySelector('#videoStatus');
    const results = rootElement.querySelector('#videoResults');
    const video = rootElement.querySelector('#ipadVideoPlayer');
    let sources = [];
    let searchSerial = 0;

    function say(message, isError = false) {
      status.textContent = message;
      status.classList.toggle('is-error', isError);
    }

    function saveSources(nextSources) {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(nextSources)); }
      catch (_) { throw new Error('Safari 未能保存视频源设置，请检查本机网站存储空间'); }
      sources = nextSources;
      renderSources();
    }

    function renderSources() {
      sourceSelect.replaceChildren();
      sourceList.replaceChildren();
      sources.forEach((source, index) => {
        const option = document.createElement('option');
        option.value = source.id;
        option.textContent = source.name;
        sourceSelect.appendChild(option);

        const row = document.createElement('div');
        row.className = 'video-source-row';
        const label = document.createElement('span');
        label.textContent = `${source.name} · 本机保存`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'secondary-button';
        remove.textContent = '移除';
        remove.addEventListener('click', () => {
          try { saveSources(sources.filter((_, sourceIndex) => sourceIndex !== index)); }
          catch (error) { say(error.message, true); return; }
          say(sources.length ? '源配置已在本机更新' : '先添加一个支持 MacCMS JSON API 且允许跨域的 HTTPS 源');
        });
        row.append(label, remove);
        sourceList.appendChild(row);
      });
      sourceSelect.disabled = sources.length === 0;
    }

    function mediaButton(episode) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary-button episode-button';
      button.textContent = isDirectMedia(episode.url) ? `▶ ${episode.name}` : `在 Safari 打开 · ${episode.name}`;
      button.addEventListener('click', () => {
        if (!isDirectMedia(episode.url)) {
          const link = document.createElement('a');
          link.href = episode.url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.hidden = true;
          rootElement.appendChild(link);
          link.click();
          link.remove();
          say('该集不是直链媒体，已尝试在 Safari 打开源页面；若未打开，请允许新标签页');
          return;
        }
        video.hidden = false;
        video.src = episode.url;
        video.load();
        video.scrollIntoView({ behavior: 'smooth', block: 'center' });
        say('已连接源站媒体；若无法播放，可能需要源站 Referer、登录或浏览器不支持该编码', false);
        video.play().catch(() => say('媒体已载入，请再点播放器的播放键；若仍失败，此源可能限制 iPad 直连', true));
      });
      return button;
    }

    function renderEpisodes(container, record, source) {
      const groups = episodeGroups(record, source.url);
      if (!groups.length && record.id) {
        const detailButton = document.createElement('button');
        detailButton.type = 'button';
        detailButton.className = 'secondary-button';
        detailButton.textContent = '读取选集';
        detailButton.addEventListener('click', async () => {
          detailButton.disabled = true;
          try {
            const detail = recordsFrom(await fetchPayload(buildApiUrl(source.url, 'detail', record.id)))[0];
            if (!detail) throw new Error('详情接口没有返回选集');
            renderEpisodes(container, detail, source);
            say('已从源站读取选集');
          } catch (error) { say(error.message, true); detailButton.disabled = false; }
        });
        container.appendChild(detailButton);
        return;
      }
      groups.forEach(group => {
        const line = document.createElement('div');
        line.className = 'video-line';
        const heading = document.createElement('strong');
        heading.textContent = group.name;
        const items = document.createElement('div');
        items.className = 'video-episodes';
        group.episodes.forEach(episode => items.appendChild(mediaButton(episode)));
        line.append(heading, items);
        container.appendChild(line);
      });
      if (!groups.length) {
        const note = document.createElement('p');
        note.className = 'video-muted';
        note.textContent = '该作品没有可识别的选集地址';
        container.appendChild(note);
      }
    }

    function renderRecords(records) {
      results.replaceChildren();
      records.forEach(record => {
        const card = document.createElement('article');
        card.className = 'video-result-card';
        if (record.poster) {
          const image = document.createElement('img');
          image.loading = 'lazy';
          image.referrerPolicy = 'no-referrer';
          image.alt = '';
          try { image.src = secureUrl(record.poster).href; } catch (_) {}
          card.appendChild(image);
        }
        const body = document.createElement('div');
        body.className = 'video-result-body';
        const title = document.createElement('h3');
        title.textContent = record.title;
        const remarks = document.createElement('p');
        remarks.textContent = record.remarks || '在线源结果';
        const episodes = document.createElement('div');
        episodes.className = 'video-episode-groups';
        renderEpisodes(episodes, record, sources.find(item => item.id === sourceSelect.value) || sources[0]);
        body.append(title, remarks, episodes);
        card.appendChild(body);
        results.appendChild(card);
      });
    }

    async function search() {
      const source = sources.find(item => item.id === sourceSelect.value);
      const query = rootElement.querySelector('#videoSearchQuery').value.trim();
      if (!source) return say('先添加视频源', true);
      if (!query) return say('请输入片名再搜索', true);
      const serial = ++searchSerial;
      const button = rootElement.querySelector('#videoSearchButton');
      button.disabled = true;
      say(`正在由 iPad 直连「${source.name}」搜索…`);
      try {
        const payload = await fetchPayload(buildApiUrl(source.url, 'list', query));
        if (serial !== searchSerial) return;
        const records = recordsFrom(payload);
        renderRecords(records);
        say(records.length ? `找到 ${records.length} 条结果；搜索请求由 iPad 直接发送` : '源站返回 0 条结果');
      } catch (error) {
        if (serial === searchSerial) { renderRecords([]); say(error.message, true); }
      } finally { if (serial === searchSerial) button.disabled = false; }
    }

    rootElement.querySelector('#videoSourceForm').addEventListener('submit', event => {
      event.preventDefault();
      const name = rootElement.querySelector('#videoSourceName');
      const url = rootElement.querySelector('#videoSourceUrl');
      try {
        const source = createSource(name.value, url.value);
        if (sources.some(item => item.url === source.url)) throw new Error('这个源地址已经添加');
        saveSources([...sources, source]);
        name.value = '';
        url.value = '';
        sourceSelect.value = source.id;
        say('源配置仅保存到本机。需要该 API 支持跨域，且接口兼容 MacCMS JSON 格式');
      } catch (error) { say(error.message, true); }
    });
    rootElement.querySelector('#videoSearchForm').addEventListener('submit', event => { event.preventDefault(); search(); });
    video.addEventListener('error', () => say('播放器无法读取该媒体；常见原因是无效链接、Referer 限制或编码不兼容', true));
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (Array.isArray(saved)) {
        sources = saved.flatMap(item => {
          try {
            const source = createSource(item.name, item.url);
            return [{ ...source, id: String(item.id || source.id) }];
          } catch (_) { return []; }
        });
      }
    } catch (_) { sources = []; }
    renderSources();
    if (!sources.length) say('添加 HTTPS MacCMS JSON API 源；配置留在 iPad，源站须允许浏览器跨域');
  }

  const apiExports = { secureUrl: value => secureUrl(value).href, createSource, buildApiUrl, recordsFrom, episodeGroups, isDirectMedia, fetchPayload };
  root.iPadOnlineVideo = apiExports;
  if (typeof module !== 'undefined' && module.exports) module.exports = apiExports;
  if (root.document) {
    const mount = () => { const target = root.document.getElementById('videoView'); if (target) api(target); };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', mount, { once: true });
    else mount();
  }
}(typeof window !== 'undefined' ? window : globalThis));

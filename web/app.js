/* No build step, external dependencies, HTML interpolation or stored secrets. */
(() => {
  'use strict';

  // Keep the entry URL's version in index.html in sync when releasing UI fixes.
  const UI_VERSION = '20261108.6';
  const POLL_MS = 5000;
  const ACCOUNT_MS = 60000;
  const COUNTRIES_MS = 300000;
  const READ_REQUEST_MS = 30000;
  const WRITE_REQUEST_MS = 130000;
  const EVENT_LIMIT = 300;
  const $ = (id) => document.getElementById(id);
  const state = {
    authenticated: false, csrf: '', epoch: 0, gateways: [], countries: [], events: [],
    gatewaysReady: false, settingsReady: false, countryData: null, after: 0,
    loads: new Map(), controllers: new Set(), timers: [], pending: new Set(),
    errors: new Map(), editing: null, deleting: null, gatewayRender: '',
    savingGateway: false, savingSettings: false, loggingIn: false, view: 'gateways',
    configSnapshot: null, configDraft: {}, configMode: 'form', configDirty: false, configLoading: false, configRevealed: false,
    gatewayCards: new Map(), eventRows: new Map(), recentRows: new Map(), frames: new Map(),
    refreshed: new Map(), eventOptionsRender: '', rawCountries: [], countrySorts: new Map(),
    countrySnapshot: '', countryRender: '', countryOptionsRender: '',
    locations: [], locationData: null, locationLoading: false, locationRequest: 0
  };
  const credentialNames = { api_key: 'API Key', seedphrase: '助记词', jwt_token: 'JWT Token', auth_code: '授权码', email: '邮箱 / 密码', password: '邮箱 / 密码' };
  const eventMessages = {
    created: '网关已创建', upstream_ready: '上游连接已就绪', stopped: '网关已停止',
    deleted: '网关已删除', updated: '网关设置已更新', network_offline: '网络离线，等待恢复',
    operation_failed: '操作失败，请稍后重试', local_bind_failed: '本地端口绑定失败，请检查占用',
    local_tls_failed: 'HTTPS 证书准备失败，请检查证书和私钥路径，或安装 OpenSSL',
    creation_backoff: '创建已退避，等待重试', owned_cleanup_pending: '旧代理资源正在回收',
    hourly_creation_limit: '已达每小时创建上限', remote_create_failed: '远端代理创建失败',
    invalid_create_response: '远端创建响应无效', duplicate_client_id: '远端返回重复的代理标识',
    candidate_probe_failed: '候选代理检查失败', upstream_probe_failed: '上游健康检查失败',
    exit_country_mismatch: '出口国家与所选国家不一致，已拦截；只重试所选国家节点',
    exit_country_unverified: '出口国家暂未验证，已暂停连接；等待国家数据恢复后重试',
    state_write_failed: '网关状态保存失败', orphan_cleanup_pending: '历史资源回收中，等待完成',
    local_credentials_required: '需通过 API 重新提供本地代理密码，已阻止免密恢复'
  };
  const statusNames = {
    running: ['运行中', 'good'], healthy: ['运行中', 'good'], ready: ['运行中', 'good'],
    active: ['运行中', 'good'], online: ['运行中', 'good'], stopped: ['已停止', 'neutral'],
    disabled: ['已停用', 'neutral'], idle: ['待启动', 'neutral'], starting: ['启动中', 'blue'],
    checking: ['检查中', 'blue'], recovering: ['恢复中', 'warning'], healing: ['恢复中', 'warning'],
    rotating: ['换线中', 'blue'], degraded: ['连接异常', 'warning'], backoff: ['退避等待', 'warning'],
    error: ['连接失败', 'bad'], failed: ['连接失败', 'bad'], offline: ['网络离线', 'warning']
  };
  const chineseNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['zh-CN'], { type: 'region' }) : null;
  const englishNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], { type: 'region' }) : null;
  const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
  const dateFormat = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false });
  const timeFormat = new Intl.DateTimeFormat('zh-CN', { hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false });
  const countryCollator = new Intl.Collator('zh-CN');
  const regionNames = new Map();
  // Provider records stay canonical for API payloads; this map only affects UI labels.
  const locationChineseNames = new Map(Object.entries({
    Alabama: '阿拉巴马州', Alaska: '阿拉斯加州', Arizona: '亚利桑那州', Arkansas: '阿肯色州',
    California: '加利福尼亚州', Colorado: '科罗拉多州', Connecticut: '康涅狄格州', Delaware: '特拉华州',
    Florida: '佛罗里达州', Georgia: '佐治亚州', Hawaii: '夏威夷州', Idaho: '爱达荷州', Illinois: '伊利诺伊州',
    Indiana: '印第安纳州', Iowa: '爱荷华州', Kansas: '堪萨斯州', Kentucky: '肯塔基州', Louisiana: '路易斯安那州',
    Maine: '缅因州', Maryland: '马里兰州', Massachusetts: '马萨诸塞州', Michigan: '密歇根州', Minnesota: '明尼苏达州',
    Mississippi: '密西西比州', Missouri: '密苏里州', Montana: '蒙大拿州', Nebraska: '内布拉斯加州', Nevada: '内华达州',
    'New Hampshire': '新罕布什尔州', 'New Jersey': '新泽西州', 'New Mexico': '新墨西哥州', 'New York': '纽约州',
    'North Carolina': '北卡罗来纳州', 'North Dakota': '北达科他州', Ohio: '俄亥俄州', Oklahoma: '俄克拉何马州',
    Oregon: '俄勒冈州', Pennsylvania: '宾夕法尼亚州', 'Rhode Island': '罗得岛州', 'South Carolina': '南卡罗来纳州',
    'South Dakota': '南达科他州', Tennessee: '田纳西州', Texas: '得克萨斯州', Utah: '犹他州', Vermont: '佛蒙特州',
    Virginia: '弗吉尼亚州', Washington: '华盛顿州', 'West Virginia': '西弗吉尼亚州', Wisconsin: '威斯康星州',
    Wyoming: '怀俄明州', 'British Columbia': '不列颠哥伦比亚省', Ontario: '安大略省', Quebec: '魁北克省',
    Osaka: '大阪府', Tokyo: '东京都', Kyoto: '京都府', Hokkaido: '北海道'
  }));
  const countrySearch = new WeakMap();
  const gatewayLiveParts = new WeakMap();

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function setText(target, value) {
    const element = typeof target === 'string' ? $(target) : target, text = String(value);
    if (element.textContent !== text) element.textContent = text;
  }

  function scheduleRender(key, render) {
    if (state.frames.has(key)) return;
    state.frames.set(key, requestAnimationFrame(() => {
      state.frames.delete(key);
      if (state.authenticated) render();
    }));
  }

  // Replace changed entries in place; leave unchanged controls attached (and focused).
  // Cache only displayed entries so removed gateways/events cannot accumulate.
  function reconcile(container, items, cache, keyOf, signatureOf, create, update) {
    const keys = new Set(items.map(keyOf));
    for (const [key, entry] of cache) {
      if (!keys.has(key)) { entry.element.remove(); cache.delete(key); }
    }
    const elements = items.map((item) => {
      const key = keyOf(item), signature = signatureOf(item);
      let entry = cache.get(key);
      if (!entry || entry.signature !== signature) {
        if (entry && update?.(entry.element, item)) entry.signature = signature;
        else {
          const element = create(item);
          if (entry?.element.parentNode === container) entry.element.replaceWith(element);
          entry = { signature, element };
          cache.set(key, entry);
        }
      }
      return entry.element;
    });
    let cursor = container.firstChild;
    for (const element of elements) {
      if (element === cursor) cursor = cursor.nextSibling;
      else container.insertBefore(element, cursor);
    }
    while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
  }

  function message(id, text, kind) {
    const element = $(id);
    element.textContent = text || '';
    element.hidden = !text;
    if (kind) element.className = `notice ${kind}`;
  }

  function safeMessage(value) {
    return String(value || '')
      .replace(/Bearer\s+[A-Za-z0-9._~+\/-]+/gi, 'Bearer [已隐藏]')
      .replace(/((?:api[_-]?key|token|seedphrase|auth_code|credential|password)\s*[=:]\s*)[^\n,;]+/gi, '$1[已隐藏]');
  }

  function eventMessage(value) {
    const text = safeMessage(value);
    const match = /^([a-z_]+?)(?:_http_(\d{3}))?$/.exec(text);
    if (!match || !Object.hasOwn(eventMessages, match[1])) return text;
    return eventMessages[match[1]] + (match[2] ? `（HTTP ${match[2]}）` : '');
  }

  function toast(text) {
    const item = node('div', 'toast', text);
    $('toasts').append(item);
    while ($('toasts').children.length > 3) $('toasts').firstElementChild.remove();
    setTimeout(() => item.remove(), 4500);
  }

  function number(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
  }

  function amount(value, unit = '') {
    const parsed = number(value);
    return parsed === null ? '—' : `${numberFormat.format(parsed)}${unit}`;
  }

  function dateText(value) {
    if (value === null || value === undefined || value === '') return '尚无记录';
    let input = value;
    if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) {
      input = Number(value) * (Number(value) < 1e12 ? 1000 : 1);
    }
    const date = new Date(input);
    return Number.isNaN(date.getTime()) ? '时间格式未知' : dateFormat.format(date);
  }

  function regionName(code, language = 'zh') {
    const normalized = String(code || '').toUpperCase();
    if (!/^[A-Z]{2}$/.test(normalized)) return normalized || '未指定';
    const names = language === 'en' ? englishNames : chineseNames;
    const key = `${language}:${normalized}`;
    if (!regionNames.has(key)) regionNames.set(key, names ? names.of(normalized) : normalized);
    return regionNames.get(key);
  }

  function countryName(country) {
    const local = regionName(country.code);
    return local === String(country.code).toUpperCase() ? country.name || local : local;
  }

  function countryMatches(country, query) {
    if (!countrySearch.has(country)) countrySearch.set(country,
      [country.code, country.name, countryName(country), regionName(country.code, 'en')].join(' ').toLowerCase());
    return countrySearch.get(country).includes(query.trim().toLowerCase());
  }

  function locationName(location) {
    const raw = String(location?.name || location?.location_name || '').trim();
    const translated = location?.name_zh || locationChineseNames.get(raw);
    return translated && translated !== raw ? `${translated} / ${raw}` : raw;
  }

  function locationRegionName(location) {
    const raw = String(location?.region || '').trim();
    const translated = location?.region_zh || locationChineseNames.get(raw);
    return translated && translated !== raw ? `${translated} / ${raw}` : raw;
  }

  function isCancelled(error) {
    return error.name === 'AbortError' || error.name === 'StaleResponse';
  }

  async function api(path, method = 'GET', body) {
    const controller = new AbortController();
    const epoch = state.epoch;
    let timedOut = false;
    state.controllers.add(controller);
    const timeout = method === 'GET' ? READ_REQUEST_MS : WRITE_REQUEST_MS;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    try {
      const headers = { Accept: 'application/json' };
      if (method !== 'GET') {
        headers['Content-Type'] = 'application/json';
        if (path !== '/api/login') {
          if (!state.csrf) throw new Error('会话尚未就绪，请重新登录。');
          headers['X-CSRF-Token'] = state.csrf;
        }
      }
      const response = await fetch(path, { method, headers, credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      let data;
      try { data = await response.json(); } catch (error) {
        if (error.name === 'AbortError') throw error;
        throw new Error(`服务响应格式异常（HTTP ${response.status}），请检查本地服务。`);
      }
      if (epoch !== state.epoch) throw Object.assign(new Error('旧会话响应已丢弃'), { name: 'StaleResponse' });
      if (!response.ok) {
        if (response.status === 401 && state.authenticated) endSession('会话已过期，请重新输入用户名和密码。');
        throw Object.assign(new Error(safeMessage(data.error) || `请求失败（HTTP ${response.status}）`), { status: response.status });
      }
      return data;
    } catch (error) {
      if (timedOut) throw new Error('请求超时；操作结果尚不确定，请刷新确认后再试。');
      if (error instanceof TypeError) throw new Error('连接本地服务失败，请确认服务仍在运行。');
      throw error;
    } finally {
      clearTimeout(timer);
      state.controllers.delete(controller);
    }
  }

  function exclusive(key, work) {
    if (state.loads.has(key)) return state.loads.get(key);
    const promise = work().finally(() => {
      if (state.loads.get(key) === promise) state.loads.delete(key);
    });
    state.loads.set(key, promise);
    return promise;
  }

  async function loadSection(key, buttonId, errorId, work) {
    if (!state.authenticated) return;
    return exclusive(key, async () => {
      const button = $(buttonId);
      button.disabled = true;
      try { await work(); state.refreshed.set(key, Date.now()); } catch (error) {
        if (!isCancelled(error) && state.authenticated) message(errorId, error.message);
      } finally { button.disabled = false; }
    });
  }

  function openLogin(text = '') {
    $('workspace').inert = true;
    message('login-error', text);
    if (!$('login-dialog').open) $('login-dialog').showModal();
  }

  function endSession(reason = '') {
    state.authenticated = false;
    state.csrf = '';
    state.epoch += 1;
    state.timers.forEach(clearInterval);
    state.timers = [];
    state.frames.forEach(cancelAnimationFrame); state.frames.clear();
    state.gatewayCards.clear(); state.eventRows.clear(); state.recentRows.clear();
    state.refreshed.clear(); state.countrySorts.clear();
    state.eventOptionsRender = ''; state.countrySnapshot = ''; state.countryRender = ''; state.countryOptionsRender = '';
    state.controllers.forEach((controller) => controller.abort());
    state.loads.clear();
    state.gateways = []; state.countries = []; state.rawCountries = []; state.events = []; state.after = 0;
    state.gatewaysReady = false; state.settingsReady = false; state.countryData = null;
    state.pending.clear(); state.errors.clear(); state.editing = null; state.deleting = null;
    state.gatewayRender = ''; state.savingGateway = false; state.savingSettings = false;
    document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
    ['gateway-list', 'country-list', 'event-list', 'recent-events', 'account-data', 'toasts'].forEach((id) => $(id).replaceChildren());
    document.querySelectorAll('.notice, .field-error').forEach((element) => { element.hidden = true; element.textContent = ''; });
    ['login-password', 'copy-value', 'gateway-name', 'gateway-search', 'country-search'].forEach((id) => { $(id).value = ''; });
    ['stat-total', 'stat-running', 'stat-countries', 'stat-connections', 'coverage-count', 'current-credential-type', 'api-base-url'].forEach((id) => { $(id).textContent = '—'; });
    document.querySelectorAll('[data-country-source], [data-country-updated]').forEach((element) => { element.textContent = '—'; });
    $('event-gateway').replaceChildren(new Option('全部网关', ''));
    $('gateway-country').replaceChildren();
    state.locations = []; state.locationData = null; state.locationRequest += 1; state.locationLoading = false;
    state.configSnapshot = null; state.configDraft = {}; state.configDirty = false; state.configLoading = false;
    document.querySelectorAll('[data-config-field]').forEach((input) => { input.value = ''; });
    $('config-source').value = '';
    ['web-server-port', 'config-saved-port'].forEach((id) => { $(id).textContent = '—'; });
    updateCredentialLock();
    openLogin(reason);
  }

  async function loadRuntime() {
    // The successful browser entry point is authoritative across NAT, TLS and
    // forwarded ports. Server route IPs are diagnostic, never replacement URLs.
    const current = new URL(window.location.href);
    document.querySelectorAll('[data-access-host]').forEach((element) => { element.textContent = current.hostname; });
    $('runtime-addresses').textContent = `${current.origin}/`;
    try {
      const info = await api('/api/runtime');
      $('runtime-server-ips').textContent = info.ips.join('、');
      const host = info.web_host.includes(':') ? `[${info.web_host}]` : info.web_host;
      $('runtime-listen').textContent = `${host}:${info.web_port}`;
      $('runtime-note').textContent = info.loopback_fallback
        ? '诊断 IP 已回退至回环地址。'
        : info.loopback_only ? '仅监听回环；远程访问需反向代理或隧道。'
          : '诊断 IP 不是代理出口；访问地址以浏览器为准。';
    } catch (error) {
      if (!isCancelled(error)) {
        $('runtime-server-ips').textContent = '获取失败';
        $('runtime-listen').textContent = '获取失败';
        $('runtime-note').textContent = `诊断信息获取失败：${error.message}`;
      }
    }
  }

  async function connect(credentials = null, initial = false) {
    if (state.loggingIn) return;
    state.loggingIn = true;
    $('login-submit').disabled = true;
    $('login-submit').textContent = credentials ? '登录中…' : '连接中…';
    $('login-username').disabled = true;
    $('login-password').disabled = true;
    $('login-status').textContent = '正在验证会话…';
    message('login-error', '');
    try {
      if (credentials) await api('/api/login', 'POST', credentials);
      credentials = null;
      $('login-password').value = '';
      const session = await api('/api/session');
      if (typeof session.csrf !== 'string' || !session.csrf) throw new Error('会话缺少校验信息，请重新登录。');
      state.csrf = session.csrf;
      state.authenticated = true;
      $('workspace').inert = false;
      $('login-dialog').close();
      loadRuntime();
      startPolling();
    } catch (error) {
      if (!isCancelled(error)) openLogin(initial && error.status === 401 ? '' : error.message);
    } finally {
      credentials = null;
      $('login-password').value = '';
      state.loggingIn = false;
      $('login-submit').disabled = false;
      $('login-submit').textContent = '登录';
      $('login-username').disabled = false;
      $('login-password').disabled = false;
      $('login-status').textContent = '';
      if ($('login-dialog').open) ($('login-username').value ? $('login-password') : $('login-username')).focus();
    }
  }

  function startPolling() {
    state.timers.forEach(clearInterval);
    syncVisible();
    state.timers = [
      setInterval(() => { if (canPoll()) { loadGateways(); loadEvents(); } }, POLL_MS),
      setInterval(() => { if (canPoll()) { loadAccount(); loadHealth(); } }, ACCOUNT_MS),
      setInterval(() => { if (canPoll()) loadCountries(); }, COUNTRIES_MS)
    ];
  }

  function canPoll() { return state.authenticated && !document.hidden; }

  function syncVisible() {
    if (!canPoll()) return;
    // Paint any response completed while hidden before waiting for the network.
    if (state.gatewaysReady) renderGateways();
    renderEvents();
    if (state.countryData) renderCountries();
    if ($('gateway-dialog').open) renderCountryOptions();
    loadGateways(); loadEvents(); loadSettings();
    const due = (key, interval) => !state.refreshed.has(key) || Date.now() - state.refreshed.get(key) >= interval;
    if (due('account', ACCOUNT_MS)) loadAccount();
    if (due('health', ACCOUNT_MS)) loadHealth();
    if (due('countries', COUNTRIES_MS)) loadCountries();
  }

  async function loadHealth() {
    return exclusive('health', async () => {
      try {
        const data = await api('/api/health');
        if (data.status !== 'ok') throw new Error('服务状态异常');
        state.refreshed.set('health', Date.now());
        $('health-state').textContent = '服务在线';
        $('health-state').className = 'health-state online';
      } catch (error) {
        if (!isCancelled(error)) {
          $('health-state').textContent = '服务离线';
          $('health-state').className = 'health-state offline';
        }
      }
    });
  }

  function loadGateways() {
    return loadSection('gateways', 'refresh-gateways', 'gateways-error', async () => {
      try {
        const data = await api('/api/gateways');
        if (!Array.isArray(data.gateways)) throw new Error('网关列表格式异常，请刷新重试。');
        state.gateways = data.gateways;
        state.gatewaysReady = true;
        message('gateways-error', '');
        $('gateway-sync').textContent = `已同步 ${timeFormat.format(new Date())} · 每 5 秒刷新`;
        renderGateways(); updateEventGatewayOptions(); renderEvents(); updateCredentialLock();
      } catch (error) {
        if (isCancelled(error) || !state.authenticated) throw error;
        state.gatewaysReady = false;
        updateCredentialLock();
        $('gateway-sync').textContent = '同步失败，5 秒后重试';
        if (!state.gateways.length) {
          state.gatewayRender = ''; state.gatewayCards.clear();
          renderEmpty($('gateway-list'), '暂未取得网关', '请检查服务后刷新。');
        }
        throw error;
      } finally { $('gateway-list').setAttribute('aria-busy', 'false'); }
    });
  }

  function renderEmpty(container, title, description, action) {
    const empty = node('div', 'empty-state');
    empty.append(node('div', 'empty-symbol', '◇'), node('h3', '', title), node('p', '', description));
    if (action) {
      const button = node('button', 'button primary', '新建网关');
      button.type = 'button'; button.addEventListener('click', () => openGateway()); empty.append(button);
    }
    container.replaceChildren(empty);
  }

  function gatewayStatus(gateway) {
    return statusNames[gateway.status] || [String(gateway.status || (gateway.enabled ? '等待启动' : '已停用')), gateway.enabled ? 'warning' : 'neutral'];
  }

  function gatewayMatches(gateway) {
    const query = $('gateway-search').value.trim().toLowerCase();
    const filter = $('gateway-filter').value;
    const matchesText = [gateway.name, gateway.port, gateway.country_code, regionName(gateway.country_code), gateway.host].join(' ').toLowerCase().includes(query);
    const matchesStatus = filter === 'all' || (filter === 'enabled' && gateway.enabled) ||
      (filter === 'stopped' && !gateway.enabled) || (filter === 'error' && (gateway.last_error || ['bad', 'warning'].includes(gatewayStatus(gateway)[1])));
    return matchesText && matchesStatus;
  }

  function renderGateways() {
    if (document.hidden) return;
    const gateways = state.gateways.filter(gatewayMatches);
    const running = state.gateways.filter((gateway) => gateway.enabled && gatewayStatus(gateway)[1] === 'good').length;
    setText('stat-total', state.gateways.length);
    setText('stat-running', running);
    setText('stat-connections', amount(state.gateways.reduce((sum, gateway) => sum + (number(gateway.connections) || 0), 0)));
    setText('gateway-count', gateways.length);
    setText('nav-gateway-count', state.gateways.length);
    if (state.view !== 'gateways') return;
    const signature = JSON.stringify([gateways, state.gateways.length, [...state.pending], [...state.errors]]);
    if (signature === state.gatewayRender) return;
    state.gatewayRender = signature;
    if (!gateways.length) {
      state.gatewayCards.clear();
      renderEmpty($('gateway-list'), state.gateways.length ? '没有匹配的网关' : '暂无网关', state.gateways.length ? '试试其他关键词或状态。' : '新建网关后即可使用代理。', !state.gateways.length);
      return;
    }
    const focused = document.activeElement;
    const focusedId = focused.closest('[data-gateway-id]')?.dataset.gatewayId;
    const focusedAction = focused.dataset.action;
    reconcile($('gateway-list'), gateways, state.gatewayCards, (gateway) => String(gateway.id),
      (gateway) => JSON.stringify([gateway, state.pending.has(String(gateway.id)), state.errors.get(String(gateway.id))]), gatewayCard, updateGatewayLive);
    if (focusedId && focusedAction) {
      const card = [...$('gateway-list').children].find((item) => item.dataset.gatewayId === focusedId);
      const control = card && [...card.querySelectorAll('button')].find((item) => item.dataset.action === focusedAction);
      if (control && !control.disabled) control.focus({ preventScroll: true });
    }
  }

  function gatewayLayout(gateway) {
    // Only telemetry can be patched without rebuilding action/address closures.
    const { connections, latency_ms, exit_ip, exit_country_code, country_source, isp, is_residential, failures, rotations, last_check, ...layout } = gateway;
    return JSON.stringify([layout, state.pending.has(String(gateway.id)), state.errors.get(String(gateway.id))]);
  }

  function updateGatewayLive(card, gateway) {
    const parts = gatewayLiveParts.get(card);
    if (!parts || parts.layout !== gatewayLayout(gateway)) return false;
    const ipStr = gateway.exit_ip ? `${gateway.exit_ip}${gateway.isp ? ' · ' + gateway.isp : ''}` : '等待连接';
    setText(parts.facts[0], ipStr);
    setText(parts.facts[1], amount(gateway.latency_ms, ' ms'));
    setText(parts.facts[2], amount(gateway.connections));
    setText(parts.facts[4], measuredCountry(gateway));
    setText(parts.facts[5], gateway.country_source || '—');
    setText(parts.meta[1], `失败 ${amount(gateway.failures)} / ${amount(gateway.failures_before_rotate)}`);
    setText(parts.meta[2], `换线 ${amount(gateway.rotations)} 次`);
    setText(parts.lastCheck, `上次检查 ${dateText(gateway.last_check)}`);
    return true;
  }

  function gatewayCard(gateway) {
    const busy = state.pending.has(String(gateway.id));
    const card = node('article', `gateway-card${busy ? ' pending' : ''}${gateway.last_error ? ' has-error' : ''}`);
    card.dataset.gatewayId = String(gateway.id); card.setAttribute('aria-busy', String(busy));
    const header = node('div', 'gateway-card-head');
    const identity = node('div', 'gateway-identity');
    const title = node('h3', '', gateway.name || '未命名网关'); title.title = gateway.name || '未命名网关';
    const modeLabel = gateway.mode === 'best' ? '自动选择' : [regionName(gateway.country_code), gateway.mode === 'country' ? '' : gateway.location_name || gateway.city || gateway.region].filter(Boolean).join(' · ');
    const resTag = gateway.is_residential ? ' · 🏠 家宽' : (gateway.exit_ip ? ' · 🏢 机房' : '');
    identity.append(title, node('p', '', modeLabel + resTag));
    const [label, tone] = gatewayStatus(gateway);
    const badge = node('span', `badge ${tone}`); badge.append(node('span', 'badge-dot'), document.createTextNode(label));
    header.append(node('span', `country-emblem${gateway.mode === 'best' ? ' auto' : ''}`, gateway.mode === 'best' ? '◎' : gateway.country_code || '—'), identity, badge);
    const facts = gatewayFacts(gateway);
    card.append(header, facts, gatewayAddresses(gateway));
    const meta = node('div', 'gateway-meta');
    meta.append(node('span', '', gateway.auto_heal ? `↻ 自动恢复 · ${amount(gateway.interval, 's')}` : '自动恢复关闭'), node('span', '', `失败 ${amount(gateway.failures)} / ${amount(gateway.failures_before_rotate)}`), node('span', '', `换线 ${amount(gateway.rotations)} 次`));
    const lastCheck = node('p', 'gateway-last-check', `上次检查 ${dateText(gateway.last_check)}`);
    card.append(meta, lastCheck, gatewayPool(gateway));
    gatewayLiveParts.set(card, { layout: gatewayLayout(gateway), facts: [...facts.querySelectorAll('dd')], meta: [...meta.children], lastCheck });
    const error = eventMessage(state.errors.get(String(gateway.id)) || gateway.last_error);
    if (error) card.append(node('p', 'notice error', error));
    if (busy) card.append(node('p', 'field-hint', '处理中…'));
    card.append(gatewayActions(gateway, busy));
    return card;
  }

  function gatewayPool(gateway) {
    const container = node('div', 'gateway-pool');
    const ready = gateway.pool_ready ?? (gateway.exit_ip ? 1 : 0), target = gateway.pool_size ?? 3;
    const labels = { ready: '就绪', filling: '补充中', depleted: '暂无可用上游', stopped: '已停止' };
    container.append(node('p', 'gateway-pool-summary', `上游可用 ${ready}/${target} · ${labels[gateway.pool_status] || '等待状态'}`));
    if (gateway.pool_error) container.append(node('p', 'notice warning', `补池：${eventMessage(gateway.pool_error)}`));
    if (Array.isArray(gateway.upstreams) && gateway.upstreams.length) {
      const details = node('details', 'gateway-upstreams'), list = node('ul', 'gateway-upstream-list');
      details.append(node('summary', '', '主备线路详情'));
      for (const member of gateway.upstreams) {
        const active = member.client_id === gateway.active_client_id;
        const status = { active: '主用', ready: '热备', suspect: '检查中', recovering: '恢复中', draining: '回收中', failed: '异常' }[member.status] || member.status || '待检测';
        const line = node('li', '', `${active ? '主用' : status} · ${member.client_id || '—'} · ${member.exit_ip || '待验证'} · ${amount(member.latency_ms, ' ms')}${member.last_error ? ' · ' + eventMessage(member.last_error) : ''}`);
        line.dataset.state = active ? 'active' : member.status || '';
        list.append(line);
      }
      details.append(list); container.append(details);
    }
    if (gateway.mode === 'region' || gateway.mode === 'city') container.append(node('p', 'field-hint',
      `实测地区 / 城市：${[gateway.exit_region, gateway.exit_city].filter(Boolean).join(' / ') || '待验证'}（位置绑定以官方 ID 为准）`));
    return container;
  }

  function gatewayFacts(gateway) {
    const facts = node('dl', 'gateway-facts');
    [['出口 IP', gateway.exit_ip ? `${gateway.exit_ip}${gateway.isp ? ' · ' + gateway.isp : ''}` : '等待连接'], ['网络延迟', amount(gateway.latency_ms, ' ms')], ['活动连接', amount(gateway.connections)], ['本地端口', gateway.port], ['实测国家', measuredCountry(gateway)], ['国家数据来源', gateway.country_source || '—']].forEach(([label, value]) => {
      const item = node('div'); item.append(node('dt', '', label), node('dd', '', value)); facts.append(item);
    });
    return facts;
  }

  function measuredCountry(gateway) {
    return gateway.exit_country_code ? `${regionName(gateway.exit_country_code)} (${gateway.exit_country_code})` : '待验证';
  }

  function gatewayAddresses(gateway) {
    const container = node('div', 'gateway-addresses');
    const isDual = ["::", "127.0.0.1", "", "dual", "all"].includes(gateway.host);
    const allowed = isDual || ['127.0.0.1', '::1', 'localhost'].includes(gateway.host);
    if (!allowed) { container.append(node('p', 'notice warning', '非标准监听，请在本地配置中核对地址。')); return container; }
    // Copy the browser-facing host, not the server's bind address or web port.
    // URL.hostname already brackets IPv6 literals.
    const host = window.location.hostname;
    const bindHost = gateway.host === '::1' ? '[::1]' : gateway.host;
    const hasAuth = Boolean(gateway.username && gateway.password);
    const authPrefix = hasAuth ? `${encodeURIComponent(gateway.username)}:${encodeURIComponent(gateway.password)}@` : '';
    for (const [label, protocol] of [['HTTP', 'http'], ['HTTPS', 'https'], ['SOCKS5', 'socks5']]) {
      const address = `${protocol}://${authPrefix}${host}:${gateway.port}`;
      const row = node('button', 'address-row'); row.type = 'button'; row.title = address;
      row.dataset.action = `copy-${protocol}`;
      row.setAttribute('aria-label', `复制 ${gateway.name} 的 ${label} 地址`);
      row.addEventListener('click', () => copyAddress(address));
      const hint = node('span', 'copy-button', '复制'); hint.setAttribute('aria-hidden', 'true');
      row.append(node('span', 'address-kind', label), node('code', '', address), hint); container.append(row);
    }
    container.append(node('p', 'field-hint', 'HTTPS 为到代理的 TLS 加密；自签名证书需导入信任，远程访问请配置匹配主机名的证书。'));
    if (hasAuth) {
      container.append(node('p', 'field-hint', '已启用代理认证'));
    }
    if (isDual && gateway.host === '::') {
      container.append(node('p', 'field-hint', `双栈全接口${hasAuth ? '' : ' · 未设置代理密码'}`));
    } else {
      container.append(node('p', 'field-hint', `实际监听：${bindHost}:${gateway.port}（回环）`));
      if (host !== bindHost) {
        container.append(node('p', 'field-hint', '连通性未验证；代理端口需单独转发。'));
      }
    }
    return container;
  }

  function gatewayActions(gateway, busy) {
    const actions = node('div', 'gateway-card-actions');
    const definitions = [[gateway.enabled ? 'stop' : 'start', gateway.enabled ? '停止' : '启动'], ['check', '检查'], ['rotate', '换线'], ['edit', '编辑'], ['logs', '日志'], ['delete', '删除']];
    for (const [action, label] of definitions) {
      const button = node('button', `${action}-action`, label);
      button.type = 'button'; button.dataset.action = action;
      button.disabled = busy || (['check', 'rotate'].includes(action) && !gateway.enabled);
      button.setAttribute('aria-label', `${label} ${gateway.name || '网关'}`);
      button.addEventListener('click', () => gatewayAction(gateway, action)); actions.append(button);
    }
    return actions;
  }

  // Ordinary HTTP does not expose Clipboard API. Selection-based copy must run
  // inside the click/touch gesture, with a rendered readonly field for mobile.
  function copyWithSelection(address) {
    if (typeof document.execCommand !== 'function') return false;
    const active = document.activeElement;
    const selection = window.getSelection();
    const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
    const fieldSelection = typeof active?.selectionStart === 'number'
      ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
    const scroll = [window.scrollX, window.scrollY];
    const buffer = node('textarea', 'clipboard-buffer');
    buffer.value = address; buffer.readOnly = true; buffer.tabIndex = -1;
    buffer.setAttribute('aria-label', '代理地址');
    document.body.append(buffer);
    try {
      buffer.focus({ preventScroll: true }); buffer.select();
      buffer.setSelectionRange(0, address.length);
      return document.execCommand('copy');
    } catch { return false; } finally {
      buffer.remove();
      if (active?.isConnected) active.focus({ preventScroll: true });
      if (selection) {
        selection.removeAllRanges();
        ranges.forEach((range) => selection.addRange(range));
      }
      if (fieldSelection && active.isConnected) active.setSelectionRange(...fieldSelection);
      window.scrollTo(...scroll);
    }
  }

  async function copyAddress(address) {
    const epoch = state.epoch;
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(address);
        copied = true;
      }
    } catch { /* Try the selection-based path before asking for manual copy. */ }
    if (epoch !== state.epoch || !state.authenticated) return;
    if (copied || copyWithSelection(address)) { toast('代理地址已复制'); return; }
    $('copy-value').value = address;
    if (!$('copy-dialog').open) $('copy-dialog').showModal();
    $('copy-value').focus(); $('copy-value').select();
    $('copy-value').setSelectionRange(0, address.length);
  }

  async function refreshAfterMutation() {
    if (state.loads.has('gateways')) await state.loads.get('gateways');
    if (state.authenticated) await loadGateways();
  }

  async function gatewayAction(gateway, action) {
    if (action === 'edit') { openGateway(gateway); return; }
    if (action === 'delete') { openDelete(gateway); return; }
    if (action === 'logs') { $('event-gateway').value = String(gateway.id); showView('events'); renderEvents(); return; }
    const id = String(gateway.id);
    if (state.pending.has(id) || !state.authenticated) return;
    state.pending.add(id); state.errors.delete(id); renderGateways(); updateCredentialLock();
    try {
      const res = await api(`/api/gateways/${encodeURIComponent(id)}/${action}`, 'POST', {});
      await refreshAfterMutation(); loadEvents();
      if (state.authenticated) {
        if (res?.last_error) {
          toast(eventMessage(res.last_error));
        } else if (action === 'rotate' && res && res.exit_ip) {
          toast(`换线完成！当前出口 IP: ${res.exit_ip}${res.isp ? ' (' + res.isp + ')' : ''}`);
        } else {
          toast({ start: '启动请求已完成', stop: '停止请求已完成', rotate: '换线成功，已切换至新出口 IP', check: '健康检查已完成' }[action]);
        }
      }
    } catch (error) {
      if (!isCancelled(error) && state.authenticated) state.errors.set(id, error.message);
    } finally {
      state.pending.delete(id);
      if (state.authenticated) renderGateways();
      updateCredentialLock();
    }
  }

  function loadCountries() {
    return loadSection('countries', 'refresh-countries', 'countries-error', async () => {
      try { acceptCountries(await api('/api/countries')); }
      catch (error) {
        document.querySelectorAll('[data-country-stale]').forEach((element) => { element.hidden = false; element.textContent = '同步失败，显示上次数据'; });
        if (!state.countryData) {
          state.countryRender = '';
          renderEmpty($('country-list'), '位置数据暂不可用', '请刷新重试，已有网关不受影响。');
        }
        if ($('gateway-dialog').open) message('country-picker-error', error.message);
        throw error;
      } finally { $('country-list').setAttribute('aria-busy', 'false'); }
    });
  }

  function sortCountries(countries, sortKey = 'providers-desc') {
    const list = [...countries];
    switch (sortKey) {
      case 'providers-desc':
        return list.sort((a, b) => (b.providers - a.providers) || countryCollator.compare(countryName(a), countryName(b)));
      case 'providers-asc':
        return list.sort((a, b) => (a.providers - b.providers) || countryCollator.compare(countryName(a), countryName(b)));
      case 'name-asc':
        return list.sort((a, b) => countryCollator.compare(countryName(a), countryName(b)));
      case 'code-asc':
        return list.sort((a, b) => a.code.localeCompare(b.code));
      default:
        return list.sort((a, b) => (b.providers - a.providers) || countryCollator.compare(countryName(a), countryName(b)));
    }
  }

  function sortedCountries(sortKey) {
    if (!state.countrySorts.has(sortKey)) state.countrySorts.set(sortKey, sortCountries(state.rawCountries, sortKey));
    return state.countrySorts.get(sortKey);
  }

  function acceptCountries(data) {
    if (!Array.isArray(data.countries)) throw new Error('国家列表格式异常，请刷新重试。');
    state.countryData = data;
    const signature = JSON.stringify(data.countries);
    if (signature !== state.countrySnapshot) {
      state.countrySnapshot = signature;
      state.rawCountries = data.countries;
      state.countrySorts.clear();
    }
    const sortVal = $('country-sort')?.value || 'providers-desc';
    state.countries = sortedCountries(sortVal);
    $('stat-countries').textContent = state.countries.length;
    $('coverage-count').textContent = state.countries.length;
    document.querySelectorAll('[data-country-source]').forEach((element) => { element.textContent = data.source || '未提供来源'; });
    document.querySelectorAll('[data-country-updated]').forEach((element) => { element.textContent = data.updated_at ? dateText(data.updated_at) : '未提供更新时间'; });
    document.querySelectorAll('[data-country-stale]').forEach((element) => {
      element.hidden = !data.stale && !data.error;
      element.className = `notice ${data.stale || data.error ? 'warning' : 'positive'}`;
      element.textContent = data.stale ? '缓存已过期 · 请刷新核对' : data.error ? '刷新失败 · 当前为缓存快照' : '数据快照未过期';
    });
    message('countries-error', safeMessage(data.error));
    renderCountries();
    if ($('gateway-dialog').open) renderCountryOptions();
  }

  function renderCountries() {
    if (state.view !== 'countries' || document.hidden) return;
    const sortVal = $('country-sort')?.value || 'providers-desc';
    state.countries = sortedCountries(sortVal);
    const countries = state.countries.filter((country) => countryMatches(country, $('country-search').value));
    setText('country-result-count', `${countries.length} / ${state.countries.length} 个地区`);
    const signature = JSON.stringify([countries, state.countries.length]);
    if (signature === state.countryRender) return;
    state.countryRender = signature;
    if (!countries.length) { renderEmpty($('country-list'), state.countries.length ? '没有匹配的国家' : '当前没有可用国家', '试试其他名称或国家代码。'); return; }
    $('country-list').replaceChildren(...countries.map((country) => {
      const card = node('article', 'country-card');
      const heading = node('div', 'country-card-head');
      const name = node('div'); name.append(node('h3', '', countryName(country)), node('p', '', country.name || regionName(country.code, 'en')));
      heading.append(node('span', 'country-emblem', country.code), name);
      const providers = node('div', 'country-provider-count'); providers.append(node('span', '', '节点数'), node('strong', '', amount(country.providers)));
      const flags = node('div', 'country-flags');
      if (country.stable) flags.append(node('span', 'badge good', '稳定位置'));
      if (country.strong_privacy) flags.append(node('span', 'badge blue', '强隐私'));
      card.append(heading, providers, flags); return card;
    }));
  }

  function renderCountryOptions(preferred) {
    if (document.hidden) return;
    const selected = preferred === undefined ? $('gateway-country').value : preferred;
    // The catalog may drop or replace an ID while an edit dialog stays open.
    // Keep identity on the option rather than looking it up in the new catalog.
    const selectedCode = state.countries.find((country) => String(country.location_id) === String(selected))?.code
      || (preferred === undefined ? $('gateway-country').selectedOptions[0]?.dataset.countryCode : '');
    const sortVal = $('gateway-country-sort')?.value || $('country-sort')?.value || 'providers-desc';
    const countries = sortedCountries(sortVal).filter((country) => countryMatches(country, $('gateway-country-search').value));
    const options = countries.map((country) => [`${countryName(country)} (${country.code}) · ${amount(country.providers)} 节点`, String(country.location_id), country.code]);
    const current = savedGatewayCountry();
    if (current && !state.countries.some((country) => country.code === current.code) && !$('gateway-country-search').value) {
      options.push([`${regionName(current.code)} (${current.code}) · 已保存，暂不可用`, current.location_id, current.code]);
    }
    const signature = JSON.stringify(options);
    if (signature !== state.countryOptionsRender) {
      state.countryOptionsRender = signature;
      $('gateway-country').replaceChildren(new Option('请选择国家 / 地区', ''), ...options.map(([label, value, code]) => {
        const option = new Option(label, value); option.dataset.countryCode = code; return option;
      }));
    }
    const code = selectedCode || (current?.location_id === String(selected) ? current.code : '');
    const replacement = options.find(([, , countryCode]) => countryCode === code);
    $('gateway-country').value = options.some(([, value]) => value === String(selected)) ? String(selected) : replacement?.[1] || '';
    if (selected && !$('gateway-country').value && $('gateway-dialog').open) {
      resetLocations();
    }
    const data = state.countryData;
    $('country-picker-note').textContent = data ? `${countries.length} 个匹配地区${data.stale ? ' · 缓存已过期' : ''}` : '正在获取地区，请稍候。';
    message('country-picker-error', data?.error ? safeMessage(data.error) : '');
  }

  function openGateway(gateway = null) {
    state.editing = gateway ? { ...gateway } : null;
    $('gateway-form').reset();
    $('gateway-fields').disabled = false;
    $('gateway-save').disabled = false;
    $('gateway-dialog-heading').textContent = gateway ? '编辑网关' : '新建网关';
    $('gateway-save').textContent = gateway ? '保存更改' : '创建并启动';
    $('gateway-name').value = gateway?.name || '';
    if ($('gateway-host')) $('gateway-host').value = gateway?.host || '::';
    const used = new Set(state.gateways.map((item) => Number(item.port)));
    let port = Number($('gateway-port').defaultValue); while (used.has(port) && port < 65535) port += 1;
    $('gateway-port').value = gateway?.port ?? port;
    if ($('gateway-username')) $('gateway-username').value = gateway?.username || '';
    if ($('gateway-password')) $('gateway-password').value = gateway?.password || '';
    $('gateway-mode').value = gateway?.mode || 'country';
    $('gateway-pool-size').value = gateway?.pool_size ?? 3;
    state.locationRequest += 1; state.locationLoading = false; state.locationData = null;
    $('gateway-location-search').disabled = false;
    const savedLocation = savedGatewayLocation();
    state.locations = savedLocation ? [savedLocation] : [];
    $('gateway-location-query').value = gateway?.location_name || gateway?.city || gateway?.region || '';
    $('gateway-region-filter').replaceChildren(new Option('全部地区', ''));
    renderLocationOptions(gateway?.location_id || '');
    if ($('gateway-residential-only')) $('gateway-residential-only').checked = gateway?.residential_only ?? true;
    $('gateway-auto-heal').checked = gateway?.auto_heal ?? true;
    $('gateway-interval').value = gateway?.interval ?? 30;
    $('gateway-failures').value = gateway?.failures_before_rotate ?? 3;
    ['gateway-error', 'port-error', 'country-picker-error', 'location-picker-error'].forEach((id) => message(id, ''));
    $('gateway-port').removeAttribute('aria-invalid');
    renderCountryOptions(gateway?.mode === 'country' ? gateway.location_id : state.countries.find((c) => c.code === gateway?.country_code)?.location_id || savedGatewayCountry()?.location_id || ''); syncGatewayMode();
    $('gateway-dialog').showModal(); $('gateway-name').focus();
    loadGatewayRegions();
    if (!state.countryData) loadCountries();
  }

  function resetLocations() {
    state.locationRequest += 1; state.locationLoading = false;
    state.locations = []; state.locationData = null;
    $('gateway-region-filter').replaceChildren(new Option('全部地区', ''));
    $('gateway-location-search').disabled = false;
    $('gateway-save').disabled = state.savingGateway;
    message('location-picker-error', '');
    renderLocationOptions('');
  }

  function syncGatewayMode(reset = false) {
    const mode = $('gateway-mode').value, best = mode === 'best';
    $('country-picker').hidden = best;
    $('gateway-country').disabled = best;
    $('gateway-country-search').disabled = best;
    $('location-picker').hidden = !['region', 'city'].includes(mode);
    $('location-region-filter').hidden = mode !== 'city';
    $('location-search-controls').hidden = mode !== 'city';
    $('location-region-controls').hidden = mode !== 'region';
    $('gateway-location-label').textContent = mode === 'region' ? '选择可用地区（州 / 省 / 县）' : '选择精确城市';
    $('gateway-mode-hint').textContent = mode === 'region'
      ? '地区按州 / 省 / 县筛选，覆盖该行政区的可用节点；城市模式用于精确到单个城市。'
      : mode === 'city'
        ? '城市模式精确到单个城市；如需覆盖整个州 / 省 / 县，请选择地区模式。'
        : '地区覆盖州 / 省 / 县范围，城市用于更精确的单城市定位。';
    if (reset) resetLocations();
    else renderLocationOptions();
    if (reset) loadGatewayRegions();
  }

  function selectedGatewayCountry() {
    const selected = $('gateway-country').value;
    const saved = savedGatewayCountry();
    return state.countries.find((country) => String(country.location_id) === selected)
      || (saved?.location_id === selected ? saved : null);
  }

  function savedGatewayCountry() {
    const current = state.editing, mode = $('gateway-mode').value;
    if (!current?.country_code || mode === 'best' || (mode === 'country' && current.mode !== 'country')) return null;
    return { code: current.country_code, location_id: current.mode === 'country' ? String(current.location_id) : `saved-country:${current.country_code}` };
  }

  function savedGatewayLocation() {
    const current = state.editing;
    if (!current || !['city', 'region'].includes(current.mode) || current.mode !== $('gateway-mode').value) return null;
    return { location_id: current.location_id, location_type: current.mode, country_code: current.country_code,
      name: current.location_name || current.city || current.region || current.location_id,
      city: current.city || '', region: current.region || '', region_location_id: current.region_location_id || '', current: true };
  }

  function renderLocationOptions(preferred) {
    const region = $('gateway-mode').value === 'region', country = selectedGatewayCountry();
    const selected = preferred ?? $('gateway-location').value, parent = $('gateway-region-filter').value;
    const records = state.locations.filter((loc) => !parent || loc.region_location_id === parent);
    const empty = region ? country ? '当前国家无可用地区，请刷新或更换国家' : '请先选择国家' : '无匹配位置，请搜索或更换关键词';
    $('gateway-location').replaceChildren(new Option(state.locationLoading ? region ? '正在加载地区…' : '正在搜索…' : records.length ? '请选择具体位置' : empty, ''),
      ...records.map((loc) => new Option(`${locationName(loc)}${loc.region && loc.region !== loc.name ? ' · ' + locationRegionName(loc) : ''} (${loc.country_code})${loc.current ? ' · 当前绑定' : ' · 节点 ' + loc.provider_count}`, loc.location_id)));
    $('gateway-location').value = records.some((loc) => loc.location_id === selected) ? selected : '';
    $('gateway-location').setAttribute('aria-busy', String(state.locationLoading));
    $('gateway-regions-refresh').disabled = !country || state.locationLoading || state.savingGateway;
    $('gateway-location-search').disabled = state.locationLoading || state.savingGateway;
    if (region) {
      $('location-picker-note').textContent = !country ? '请先选择国家，随后自动显示可用地区。' : state.locationLoading
        ? '正在加载该国家的官方可用地区…' : state.locationData?.stale
          ? '地区列表加载失败或已过期，显示上次结果；请刷新地区后再选择。当前绑定可保持不变。'
          : state.locationData ? `${records.filter((loc) => loc.provider_count > 0).length} 个可用地区 · 来源 ${state.locationData.source} · ${dateText(state.locationData.updated_at)}`
            : '选择国家后自动加载可用地区；当前绑定可保持不变。';
      return;
    }
    $('location-picker-note').textContent = state.locationLoading ? '正在查询官方位置…' : state.locationData?.stale
      ? '位置快照已过期，显示上次结果；请重新搜索后再选择。'
      : state.locationData ? `${records.length} 个匹配位置 · 来源 ${state.locationData.source} · ${dateText(state.locationData.updated_at)}`
        : '先选择国家，再搜索并选择具体位置；当前绑定可保持不变。';
  }

  function loadGatewayRegions(refresh = false) {
    if ($('gateway-mode').value === 'region' && selectedGatewayCountry() && $('gateway-dialog').open) {
      searchGatewayLocations({ refresh });
    }
  }

  async function searchGatewayLocations({ refresh = true } = {}) {
    if (state.locationLoading || state.savingGateway) return;
    const country = selectedGatewayCountry(), mode = $('gateway-mode').value;
    if (!['region', 'city'].includes(mode)) return;
    if (!country) { message('location-picker-error', '请先选择出口国家。'); return; }
    const query = mode === 'region' ? country.name || regionName(country.code, 'en') : $('gateway-location-query').value.trim();
    if (!query) { message('location-picker-error', '请输入城市或地区关键词。'); return; }
    const request = ++state.locationRequest;
    state.locationLoading = true; $('gateway-location-search').disabled = true; $('gateway-save').disabled = true;
    message('location-picker-error', ''); renderLocationOptions();
    try {
      const params = new URLSearchParams({ query, country_code: country.code, type: mode, refresh: refresh ? '1' : '0' });
      const data = await api(`/api/locations?${params}`);
      if (request !== state.locationRequest || !$('gateway-dialog').open) return;
      if (!Array.isArray(data.locations)) throw new Error('位置列表格式异常，请重试。');
      const selected = $('gateway-location').value, parent = $('gateway-region-filter').value;
      state.locations = mode === 'region' ? data.locations.filter((loc) => loc.location_type === 'region'
        && loc.country_code === country.code && Number.isInteger(loc.provider_count) && loc.provider_count > 0) : [...data.locations];
      state.locationData = data;
      const current = savedGatewayLocation();
      if (current?.country_code === country.code) {
        const index = state.locations.findIndex((loc) => loc.location_id === current.location_id);
        if (index < 0) state.locations.push(current);
        else state.locations[index] = { ...state.locations[index], ...current };
      }
      const parents = new Map(state.locations.filter((loc) => loc.region_location_id).map((loc) => [loc.region_location_id, locationRegionName(loc) || locationName(loc)]));
      $('gateway-region-filter').replaceChildren(new Option('全部地区', ''), ...[...parents].map(([id, name]) => new Option(name, id)));
      if (parents.has(parent)) $('gateway-region-filter').value = parent;
      if (data.error) message('location-picker-error', data.error);
      else if (!(mode === 'region' ? state.locations.some((loc) => loc.provider_count > 0) : data.locations.length)) {
        message('location-picker-error', mode === 'region'
          ? '该国家当前无可用地区，请刷新或更换国家；不会自动改为全国出口。'
          : '该国家下无匹配位置，请修改关键词；不会自动改为全国出口。');
      }
      renderLocationOptions(selected);
    } catch (error) {
      if (request === state.locationRequest && !isCancelled(error)) {
        state.locationData = { ...(state.locationData || {}), stale: true }; message('location-picker-error', error.message);
      }
    } finally {
      if (request === state.locationRequest) {
        state.locationLoading = false; $('gateway-location-search').disabled = false; $('gateway-save').disabled = state.savingGateway;
        renderLocationOptions();
      }
    }
  }

  function gatewayPayload() {
    const portText = $('gateway-port').value.trim();
    const port = Number(portText);
    const duplicate = state.gateways.some((item) => Number(item.port) === port && String(item.id) !== String(state.editing?.id));
    const error = !/^\d+$/.test(portText) || port < 1 || port > 65535 ? '端口必须是 1–65535 的整数。' : duplicate ? '该端口已被其他网关使用，请选择不同端口。' : '';
    message('port-error', error);
    $('gateway-port').setAttribute('aria-invalid', String(Boolean(error)));
    if (error) { $('gateway-port').focus(); return null; }
    if (!$('gateway-form').reportValidity()) return null;
    const name = $('gateway-name').value.trim();
    if (!name) throw new Error('请填写网关名称。');
    const host = $('gateway-host')?.value || '::';
    const username = $('gateway-username')?.value.trim() || '';
    const password = $('gateway-password')?.value || '';
    if (Boolean(username) !== Boolean(password)) {
      throw new Error('代理用户名与密码必须同时提供，或同时留空。');
    }
    const interval = Number($('gateway-interval').value);
    const failures = Number($('gateway-failures').value);
    if (!Number.isInteger(interval) || interval < 5 || interval > 3600) throw new Error('检查间隔必须是 5–3600 秒的整数。');
    if (!Number.isInteger(failures) || failures < 1 || failures > 100) throw new Error('连续失败阈值必须是 1–100 的整数。');
    const mode = $('gateway-mode').value, poolSize = Number($('gateway-pool-size').value);
    if (!Number.isInteger(poolSize) || poolSize < 1 || poolSize > 20) throw new Error('上游代理数量须为 1–20 的整数。');
    let country = null, location = null;
    if (mode !== 'best') {
      const selected = $('gateway-country').value;
      country = selectedGatewayCountry();
      if (!country || !selected) { message('country-picker-error', '请选择国家 / 地区；若列表为空，请刷新位置数据。'); $('gateway-country').focus(); return null; }
    }
    if (['region', 'city'].includes(mode)) {
      location = state.locations.find((loc) => loc.location_id === $('gateway-location').value && loc.country_code === country?.code);
      if (!location || state.locationLoading || (state.locationData?.stale && !location.current)) {
        message('location-picker-error', mode === 'region'
          ? '请选择该国家下的可用地区；过期数据需刷新地区后再选择。'
          : '请搜索并选择该国家下的精确位置；过期数据需重新搜索。'); $('gateway-location').focus(); return null;
      }
    }
    return { name, host, port, mode, pool_size: poolSize, country_code: country?.code || '', location_id: location?.location_id || country?.location_id || '', location_name: location?.name || '', region: location?.region || '', city: location?.city || '', region_location_id: location?.region_location_id || '', auto_heal: $('gateway-auto-heal').checked, residential_only: Boolean($('gateway-residential-only')?.checked ?? true), interval, failures_before_rotate: failures, enabled: state.editing?.enabled ?? true, username, password };
  }

  async function saveGateway(event) {
    event.preventDefault();
    if (state.savingGateway || !state.authenticated) return;
    message('gateway-error', '');
    let payload;
    try { payload = gatewayPayload(); } catch (error) { message('gateway-error', error.message); return; }
    if (!payload) return;
    state.savingGateway = true;
    const editing = state.editing;
    const id = editing ? String(editing.id) : '';
    if (id) state.pending.add(id);
    $('gateway-fields').disabled = true; $('gateway-save').disabled = true;
    $('gateway-save').textContent = '正在保存…';
    updateCredentialLock();
    try {
      const result = await api(editing ? `/api/gateways/${encodeURIComponent(id)}` : '/api/gateways', editing ? 'PUT' : 'POST', payload);
      $('gateway-dialog').close();
      await refreshAfterMutation(); loadEvents();
      if (state.authenticated) toast(result?.last_error ? eventMessage(result.last_error) : editing ? '网关设置已保存' : '网关已创建');
    } catch (error) {
      if (!isCancelled(error) && state.authenticated) message('gateway-error', error.message);
    } finally {
      state.savingGateway = false; if (id) state.pending.delete(id);
      $('gateway-fields').disabled = false; $('gateway-save').disabled = false;
      $('gateway-save').textContent = editing ? '保存更改' : '创建并启动';
      syncGatewayMode(); updateCredentialLock();
      if (state.authenticated) renderGateways();
    }
  }

  function openDelete(gateway) {
    state.deleting = gateway;
    $('delete-description').textContent = `即将删除「${gateway.name || '未命名网关'}」，本地端口 ${gateway.port}。`;
    message('delete-error', ''); $('confirm-delete').disabled = false;
    $('delete-dialog').showModal(); $('delete-cancel').focus();
  }

  async function deleteGateway() {
    const gateway = state.deleting;
    if (!gateway || !state.authenticated || state.pending.has(String(gateway.id))) return;
    const id = String(gateway.id);
    state.pending.add(id); $('confirm-delete').disabled = true; $('delete-cancel').disabled = true;
    message('delete-error', ''); updateCredentialLock();
    try {
      await api(`/api/gateways/${encodeURIComponent(id)}`, 'DELETE');
      $('delete-dialog').close(); state.deleting = null; state.errors.delete(id);
      await refreshAfterMutation(); loadEvents();
      if (state.authenticated) toast('网关已删除；旧代理资源由服务端回收');
    } catch (error) {
      if (!isCancelled(error) && state.authenticated) message('delete-error', error.message);
    } finally {
      state.pending.delete(id); $('confirm-delete').disabled = false; $('delete-cancel').disabled = false;
      updateCredentialLock(); if (state.authenticated) renderGateways();
    }
  }

  function loadAccount() {
    return loadSection('account', 'refresh-account', 'account-error', async () => {
      try { renderAccount(await api('/api/account')); message('account-error', ''); }
      catch (error) { $('account-data').replaceChildren(node('p', 'muted', '配额暂不可用')); throw error; }
      finally { $('account-data').setAttribute('aria-busy', 'false'); }
    });
  }

  function renderAccount(data) {
    const title = node('p', 'quota-title', '今日剩余流量');
    const remaining = node('div', 'quota-number', amount(data.remaining_gib, ' GiB'));
    const progress = node('progress', 'quota-track');
    progress.max = Math.max(1, number(data.daily_quota_gib) || 0);
    progress.value = Math.min(progress.max, Math.max(0, number(data.used_gib) || 0));
    progress.setAttribute('aria-label', `今日已用 ${amount(data.used_gib)} GiB，总配额 ${amount(data.daily_quota_gib)} GiB`);
    const values = node('div', 'quota-numbers'); values.append(node('span', '', `已用 ${amount(data.used_gib, ' GiB')}`), node('span', '', `共 ${amount(data.daily_quota_gib, ' GiB')}`));
    const details = node('div', 'account-details');
    details.append(node('p', '', data.network_name || '当前账户'), node('p', 'muted', `${data.is_guest ? '访客账户' : '已连接账户'} · ${data.plan_tier || '套餐未提供'}`), node('p', 'muted', `更新于 ${timeFormat.format(new Date())}`));
    $('account-data').replaceChildren(title, remaining, progress, values, details);
  }

  // Configuration drafts stay in memory; the server never returns existing secrets.
  function configInputs() { return [...document.querySelectorAll('[data-config-field]')]; }

  const SECRET_FIELDS = ['web_password', 'api_key', 'jwt_token', 'seedphrase', 'auth_code', 'email', 'password'];

  function writeConfigForm() {
    configInputs().forEach((input) => {
      const field = input.dataset.configField;
      const rawVal = state.configDraft[field];
      if (SECRET_FIELDS.includes(field)) {
        if (rawVal === '__KEEP_EXISTING__' || (state.configSnapshot && state.configSnapshot.config && state.configSnapshot.config[field] === '__KEEP_EXISTING__')) {
          input.value = '';
          input.dataset.hasSaved = 'true';
          input.placeholder = field === 'web_password' ? '•••••••• (已保存，留空保持原密码)' : '•••••••• (已配置，留空保持原值)';
        } else {
          input.value = rawVal ?? '';
          input.dataset.hasSaved = (rawVal && rawVal !== '') ? 'true' : 'false';
          input.placeholder = '未配置';
        }
      } else {
        input.value = rawVal ?? '';
      }
    });
  }

  function collectConfigForm() {
    const draft = {};
    configInputs().forEach((input) => {
      const key = input.dataset.configField;
      if (SECRET_FIELDS.includes(key)) {
        if (input.value === '' && input.dataset.hasSaved === 'true') {
          draft[key] = '__KEEP_EXISTING__';
        } else {
          draft[key] = input.value;
        }
      } else {
        draft[key] = input.type === 'number' && input.value !== '' ? Number(input.value) : input.value;
      }
    });
    return draft;
  }

  function configLines() { $('config-line-count').textContent = `${$('config-source').value.split('\n').length} 行`; }

  function setConfigMode(mode) {
    state.configMode = mode;
    ['form', 'source'].forEach((name) => {
      const tab = $(`config-tab-${name}`);
      tab.setAttribute('aria-selected', String(mode === name));
      tab.tabIndex = mode === name ? 0 : -1;
    });
    $('config-visual').hidden = mode !== 'form';
    $('config-code').hidden = mode !== 'source';
    configLines();
  }

  
  function setupSecretToggles() {
    document.querySelectorAll('.toggle-secret-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const targetId = btn.dataset.target;
        const input = $(targetId);
        if (!input) return;
        const eye = btn.querySelector('.icon-eye');
        const eyeOff = btn.querySelector('.icon-eye-off');
        if (input.type === 'text') {
          input.type = 'password';
          if (eye) eye.hidden = false;
          if (eyeOff) eyeOff.hidden = true;
        } else {
          if (!input.value && input.dataset.hasSaved === 'true') {
            const field = input.dataset.configField;
            try {
              btn.disabled = true;
              const res = await api(`/api/config/reveal?field=${encodeURIComponent(field)}`);
              input.value = res.value || '';
              if (res.value) {
                toast('已显示保存的内容');
              } else {
                toast('该字段暂无已保存内容');
              }
            } catch (err) {
              toast('获取内容失败: ' + err.message);
            } finally {
              btn.disabled = false;
            }
          }
          input.type = 'text';
          if (eye) eye.hidden = true;
          if (eyeOff) eyeOff.hidden = false;
        }
      });
    });
  }

  function acceptConfiguration(data) {
    if (!data.config || typeof data.source !== 'string' || typeof data.revision !== 'string') throw new Error('配置响应格式异常。');
    state.configSnapshot = data;
    state.configDraft = { ...data.config };
    state.settingsReady = true;
    state.configDirty = false;
    writeConfigForm(); $('config-source').value = data.source; configLines();
    const kind = ['api_key', 'jwt_token', 'auth_code', 'email', 'password', 'seedphrase'].find((key) => data.config[key]);
    $('current-credential-type').textContent = credentialNames[kind] || '未配置';
    $('configured-badge').textContent = kind ? '已配置' : '未配置';
    $('configured-badge').className = `badge ${kind ? 'good' : 'neutral'}`;
    $('api-base-url').textContent = data.config.api_base_url;
    $('web-server-port').textContent = String(data.active_port);
    $('config-saved-port').textContent = String(data.config.web_port);
    message('config-restart', data.restart_required ? '业务配置已生效；管理服务监听地址/端口需重启后更新。' : '', 'warning');
    message('config-legacy', data.legacy_credentials ? '检测到旧版独立凭据：本次保存会迁入 config.json，并在 .local 中保留原凭据备份。' : '', 'warning');
    updateCredentialLock();
  }

  function loadSettings(force = false) {
    // Navigation/polling must never replace an in-progress draft.
    if (!state.authenticated || state.savingSettings || (state.configSnapshot && !force)) return Promise.resolve();
    return loadSection('settings', 'refresh-settings', 'settings-load-error', async () => {
      state.configLoading = true; updateCredentialLock();
      try {
        acceptConfiguration(await api('/api/config'));
        message('settings-load-error', ''); message('settings-error', ''); message('settings-success', '');
      } finally { state.configLoading = false; updateCredentialLock(); }
    });
  }

  function updateCredentialLock() {
    const locked = !state.authenticated || !state.settingsReady || state.savingSettings || state.configLoading;
    $('settings-fields').disabled = locked;
    ['settings-save', 'config-tab-form', 'config-tab-source', 'config-discard'].forEach((id) => { $(id).disabled = locked; });
    $('refresh-settings').disabled = state.savingSettings || state.configLoading;
    $('credential-lock').textContent = state.gateways.length ? `当前 ${state.gateways.length} 个网关；更换认证或 API 前需删除并完成回收。` : '更换认证或 API 前需完成旧代理回收。';
    $('config-dirty').textContent = state.savingSettings ? '正在处理…' : state.configLoading ? '正在读取…' : !state.settingsReady ? '等待读取' : state.configDirty ? '有未保存修改' : '已与文件同步';
    $('config-dirty').className = `badge ${state.configDirty ? 'warning' : state.settingsReady ? 'good' : 'neutral'}`;
  }

  function markConfigDirty() {
    state.configDirty = true;
    if (state.configMode === 'form') state.configDraft = collectConfigForm();
    message('settings-success', ''); message('settings-error', '');
    configLines(); updateCredentialLock();
  }

  async function switchConfigMode(mode, format = false) {
    if (state.savingSettings || !state.settingsReady || (mode === state.configMode && !format)) return;
    message('settings-error', '');
    const payload = { source: $('config-source').value, revision: state.configSnapshot.revision };
    if (state.configMode === 'form') payload.fields = collectConfigForm();
    state.savingSettings = true; updateCredentialLock();
    try {
      const result = await api('/api/config/validate', 'POST', payload);
      state.configDraft = result.config;
      writeConfigForm();
      $('config-source').value = result.source;
      if (format) state.configDirty = true;
      setConfigMode(mode);
      if (format) message('settings-success', '校验通过，源码已格式化；尚未写入文件。', 'positive');
    } catch (error) {
      if (!isCancelled(error) && state.authenticated) message('settings-error', error.message);
    } finally { state.savingSettings = false; updateCredentialLock(); }
  }

  function confirmConfigDiscard() {
    return !state.configDirty || window.confirm('有未保存的配置修改，确认丢弃吗？');
  }

  async function saveSettings(event) {
    event.preventDefault();
    if (state.savingSettings || !state.settingsReady || state.configLoading) return;
    if (state.configMode === 'form' && !$('settings-form').reportValidity()) return;
    const payload = { revision: state.configSnapshot.revision };
    payload.source = $('config-source').value;
    if (state.configMode === 'form') payload.fields = collectConfigForm();
    state.savingSettings = true; updateCredentialLock();
    $('settings-save').textContent = '正在保存…';
    message('settings-error', ''); message('settings-success', '');
    try {
      const result = await api('/api/config', 'PUT', payload);
      acceptConfiguration(result);
      if (result.restart_required) {
        message('settings-success', '配置已保存并立即生效（管理服务监听端口/地址需重启生效）。', 'positive');
      } else {
        message('settings-success', '配置已保存并立即生效，无需重启！', 'positive');
      }
    } catch (error) {
      if (!isCancelled(error) && state.authenticated) message('settings-error', error.message);
    } finally {
      state.savingSettings = false; updateCredentialLock(); $('settings-save').textContent = '保存配置';
    }
  }

  function loadEvents() {
    return loadSection('events', 'refresh-events', 'events-error', async () => {
      try {
        const data = await api(`/api/events?after=${state.after}`);
        if (!Array.isArray(data.events)) throw new Error('事件列表格式异常，请稍后重试。');
        if (data.events.length) {
          const events = new Map(state.events.map((event) => [Number(event.id), event]));
          for (const event of data.events) {
            const id = Number(event.id);
            if (!Number.isSafeInteger(id) || id < 0) continue;
            events.set(id, event); state.after = Math.max(state.after, id);
          }
          state.events = [...events.values()].sort((a, b) => Number(a.id) - Number(b.id)).slice(-EVENT_LIMIT);
        }
        message('events-error', ''); message('recent-events-error', '');
        $('event-sync').textContent = `已同步 ${timeFormat.format(new Date())} · 每 5 秒更新`;
        updateEventGatewayOptions(); renderEvents();
      } catch (error) {
        if (!isCancelled(error) && state.authenticated) message('recent-events-error', error.message);
        $('event-sync').textContent = '同步失败 · 5 秒后自动重试'; throw error;
      } finally { $('event-list').setAttribute('aria-busy', 'false'); }
    });
  }

  function updateEventGatewayOptions() {
    if (document.hidden) return;
    const select = $('event-gateway');
    const selected = select.value;
    const gateways = new Map(state.gateways.map((gateway) => [String(gateway.id), gateway.name]));
    state.events.forEach((event) => { if (event.gateway_id && !gateways.has(String(event.gateway_id))) gateways.set(String(event.gateway_id), `已删除网关 · ${event.gateway_id}`); });
    const signature = JSON.stringify([...gateways]);
    if (signature === state.eventOptionsRender) return;
    state.eventOptionsRender = signature;
    select.replaceChildren(new Option('全部网关', ''), ...[...gateways].map(([id, name]) => new Option(name || id, id)));
    select.value = gateways.has(selected) ? selected : '';
  }

  function eventRow(event, gatewayNames) {
    const level = event.level === 'warn' ? 'warning' : event.level;
    const row = node('div', 'event-row');
    const body = node('div', 'event-body');
    const details = node('div', 'event-details');
    const name = gatewayNames.get(String(event.gateway_id));
    details.append(node('span', '', name || (event.gateway_id ? `网关 ${event.gateway_id}` : '系统')), node('time', '', dateText(event.time)));
    body.append(node('p', 'event-message', eventMessage(event.message)), details);
    const tone = ['info', 'warning', 'error'].includes(level) ? level : 'info';
    row.append(node('span', `event-marker ${tone}`), body, node('span', 'event-level', { info: '信息', warning: '警告', error: '错误' }[level] || level || '信息'));
    return row;
  }

  function renderEvents() {
    if (document.hidden || !['events', 'gateways'].includes(state.view)) return;
    const events = [...state.events].reverse();
    const names = new Map(state.gateways.map((item) => [String(item.id), item.name]));
    const draw = (id, rows, cache) => reconcile($(id), rows, cache, (event) => Number(event.id),
      (event) => JSON.stringify([event, names.get(String(event.gateway_id))]), (event) => eventRow(event, names));
    if (state.view === 'events') {
      const gateway = $('event-gateway').value, level = $('event-level').value;
      const filtered = events.filter((event) => (!gateway || String(event.gateway_id) === gateway) && (!level || (event.level === 'warn' ? 'warning' : event.level) === level));
      if (filtered.length) draw('event-list', filtered, state.eventRows);
      else {
        state.eventRows.clear();
        if (!$('event-list').querySelector('.empty-state')) renderEmpty($('event-list'), '暂无匹配的事件', '网关操作后自动记录。');
      }
    } else if (events.length) draw('recent-events', events.slice(0, 3), state.recentRows);
    else {
      state.recentRows.clear();
      if ($('recent-events').textContent !== '暂无动态') $('recent-events').replaceChildren(node('p', 'muted', '暂无动态'));
    }
  }

  function showView(view) {
    const title = { gateways: '网关总览', countries: '国家与地区', events: '事件日志', settings: '配置面板' }[view];
    if (!title) return;
    if (state.view === 'settings' && view !== 'settings') {
      if (state.savingSettings) { toast('正在处理配置，请稍候。'); return; }
      if (!confirmConfigDiscard()) return;
      if (state.configDirty) acceptConfiguration(state.configSnapshot);
    }
    state.view = view;
    document.querySelectorAll('.view').forEach((element) => { element.hidden = element.id !== `view-${view}`; });
    document.querySelectorAll('.nav-item').forEach((button) => {
      const active = button.dataset.view === view; button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    $('breadcrumb-current').textContent = title;
    document.title = `${title} · URnetwork`;
    if (view === 'settings') loadSettings();
    if (view === 'gateways') { renderGateways(); renderEvents(); }
    if (view === 'countries') renderCountries();
    if (view === 'events') { updateEventGatewayOptions(); renderEvents(); loadEvents(); }
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $('theme-toggle').setAttribute('aria-label', theme === 'dark' ? '切换到浅色模式' : '切换到深色模式');
    $('theme-toggle').title = theme === 'dark' ? '切换到浅色模式' : '切换到深色模式';
  }

  function initTheme() {
    const preferred = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    try { const saved = localStorage.getItem('urnetwork-theme'); applyTheme(['dark', 'light'].includes(saved) ? saved : preferred); }
    catch { applyTheme(preferred); }
    $('theme-toggle').addEventListener('click', () => {
      const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; applyTheme(theme);
      try { localStorage.setItem('urnetwork-theme', theme); } catch { toast('主题已切换；浏览器未允许保存偏好。'); }
    });
  }

  async function logout() {
    if ($('logout').disabled || state.savingSettings || !confirmConfigDiscard()) return;
    $('logout').disabled = true;
    try { await api('/api/logout', 'POST', {}); endSession(); }
    catch (error) { if (!isCancelled(error) && state.authenticated) toast(`退出未完成：${error.message}`); }
    finally { $('logout').disabled = false; }
  }

  function openCleanDialog() {
    if ($('clean-status')) $('clean-status').hidden = true;
    $('clean-confirm-dialog').showModal();
  }

  async function performCleanProxies() {
    const confirmBtn = $('clean-confirm-btn');
    const cancelBtn = $('clean-cancel');
    const quickBtn = $('btn-quick-clean-proxies');
    const settingsBtn = $('btn-settings-clean-proxies');

    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = '清理中…';
    }
    if (cancelBtn) cancelBtn.disabled = true;
    if (quickBtn) quickBtn.disabled = true;
    if (settingsBtn) settingsBtn.disabled = true;

    try {
      const res = await api('/api/proxies/clean', 'POST', {});
      $('clean-confirm-dialog').close();
      const count = res.proxies_removed ?? 0;
      const resetText = res.clients_reset ? '客户端已重置' : '客户端未重置';
      toast(`已清理 ${count} 个代理；${resetText}`);
      if ($('clean-status')) {
        $('clean-status').hidden = false;
        $('clean-status').className = 'notice positive';
        $('clean-status').textContent = `已清理 ${count} 个代理；${resetText}。`;
      }
      loadAccount().catch(() => {});
      loadGateways().catch(() => {});
    } catch (err) {
      const msg = err.message || '清理异常';
      toast(`清理失败: ${msg}`);
      if ($('clean-status')) {
        $('clean-status').hidden = false;
        $('clean-status').className = 'notice error';
        $('clean-status').textContent = `清理异常: ${msg}`;
      }
    } finally {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '确认清理';
      }
      if (cancelBtn) cancelBtn.disabled = false;
      if (quickBtn) quickBtn.disabled = false;
      if (settingsBtn) settingsBtn.disabled = false;
    }
  }

  function bindEvents() {
    document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
    document.querySelector('.brand').addEventListener('click', (event) => { event.preventDefault(); showView('gateways'); });
    $('login-form').addEventListener('submit', (event) => {
      event.preventDefault();
      if ($('login-form').reportValidity()) connect({ username: $('login-username').value, password: $('login-password').value });
    });
    $('login-dialog').addEventListener('cancel', (event) => event.preventDefault());
    $('logout').addEventListener('click', logout);
    $('add-gateway').addEventListener('click', () => openGateway());
    $('gateway-form').addEventListener('submit', saveGateway);
    ['gateway-close', 'gateway-cancel'].forEach((id) => $(id).addEventListener('click', () => { if (!state.savingGateway) $('gateway-dialog').close(); }));
    $('gateway-dialog').addEventListener('cancel', (event) => { if (state.savingGateway) event.preventDefault(); });
    $('gateway-mode').addEventListener('change', () => syncGatewayMode(true));
    $('gateway-country').addEventListener('change', () => { resetLocations(); loadGatewayRegions(); });
    $('gateway-location-search').addEventListener('click', searchGatewayLocations);
    $('gateway-regions-refresh').addEventListener('click', () => loadGatewayRegions(true));
    $('gateway-region-filter').addEventListener('change', () => renderLocationOptions());
    $('gateway-location-query').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); searchGatewayLocations(); } });
    $('gateway-country-search').addEventListener('input', () => scheduleRender('country-options', renderCountryOptions));
    $('gateway-search').addEventListener('input', () => scheduleRender('gateways', renderGateways));
    $('gateway-filter').addEventListener('change', renderGateways);
    $('country-search').addEventListener('input', () => scheduleRender('countries', renderCountries));
    $('country-sort')?.addEventListener('change', renderCountries);
    $('gateway-country-sort')?.addEventListener('change', () => renderCountryOptions());
    $('btn-quick-clean-proxies')?.addEventListener('click', openCleanDialog);
    $('btn-settings-clean-proxies')?.addEventListener('click', openCleanDialog);
    $('clean-cancel')?.addEventListener('click', () => $('clean-confirm-dialog').close());
    $('clean-confirm-btn')?.addEventListener('click', performCleanProxies);
    $('refresh-gateways').addEventListener('click', loadGateways);
    $('refresh-account').addEventListener('click', loadAccount);
    $('refresh-events').addEventListener('click', loadEvents);
    $('refresh-settings').addEventListener('click', () => { if (confirmConfigDiscard()) loadSettings(true); });
    $('config-discard').addEventListener('click', () => { if (state.configSnapshot && confirmConfigDiscard()) { acceptConfiguration(state.configSnapshot); message('settings-error', ''); message('settings-success', ''); } });
    $('config-tab-form').addEventListener('click', () => switchConfigMode('form'));
    $('config-tab-source').addEventListener('click', () => switchConfigMode('source'));
    $('config-format').addEventListener('click', () => switchConfigMode('source', true));
    $('settings-fields').addEventListener('input', markConfigDirty);
    $('settings-fields').addEventListener('change', markConfigDirty);
    document.querySelector('.config-tabs').addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const mode = event.key === 'Home' ? 'form' : event.key === 'End' ? 'source' : state.configMode === 'form' ? 'source' : 'form';
      switchConfigMode(mode).then(() => $(`config-tab-${state.configMode}`).focus());
    });
    window.addEventListener('beforeunload', (event) => { if (state.configDirty || state.savingSettings) { event.preventDefault(); event.returnValue = ''; } });
    $('refresh-countries').addEventListener('click', () => loadSection('countries', 'refresh-countries', 'countries-error', async () => {
      await api('/api/countries/refresh', 'POST', {});
      acceptCountries(await api('/api/countries')); toast('位置快照已刷新');
    }));
    ['event-gateway', 'event-level'].forEach((id) => $(id).addEventListener('change', renderEvents));
    $('delete-cancel').addEventListener('click', () => $('delete-dialog').close());
    $('delete-dialog').addEventListener('cancel', (event) => { if ($('confirm-delete').disabled) event.preventDefault(); });
    $('confirm-delete').addEventListener('click', deleteGateway);
    $('copy-close').addEventListener('click', () => $('copy-dialog').close());
    $('settings-form').addEventListener('submit', saveSettings);
    setupSecretToggles();
    $('config-reveal-toggle')?.addEventListener('click', async () => {
      if (state.savingSettings || !state.settingsReady) return;
      state.configRevealed = !state.configRevealed;
      $('config-reveal-toggle').textContent = state.configRevealed ? '🙈 脱敏展示' : '👁️ 显示明文';
      try {
        state.configLoading = true;
        updateCredentialLock();
        const data = await api(`/api/config${state.configRevealed ? '?reveal=1' : ''}`);
        $('config-source').value = data.source;
        configLines();
        toast(state.configRevealed ? '已显示明文凭据' : '已恢复脱敏展示');
      } catch (err) {
        toast('切换显示失败: ' + err.message);
      } finally {
        state.configLoading = false;
        updateCredentialLock();
      }
    });
    document.addEventListener('visibilitychange', syncVisible);
  }

  // Remove legacy launch secrets without using them to authenticate.
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  if (fragment.has('token')) history.replaceState(history.state, '', window.location.pathname + window.location.search);
  initTheme(); bindEvents(); openLogin();
  document.querySelectorAll('[data-ui-version]').forEach((element) => { element.textContent = `界面版本 ${UI_VERSION}`; });
  loadRuntime();
  connect(null, true);
})();

/* No build step, external dependencies, HTML interpolation or stored secrets. */
(() => {
  'use strict';

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
    configSnapshot: null, configDraft: {}, configMode: 'form', configDirty: false, configLoading: false
  };
  const credentialNames = { api_key: 'API Key', seedphrase: '助记词', jwt_token: 'JWT Token', auth_code: '授权码', email: '邮箱 / 密码', password: '邮箱 / 密码' };
  const eventMessages = {
    created: '网关已创建', upstream_ready: '上游连接已就绪', stopped: '网关已停止',
    deleted: '网关已删除', updated: '网关设置已更新', network_offline: '网络离线，等待恢复',
    operation_failed: '操作失败，请稍后重试', local_bind_failed: '本地端口绑定失败，请检查占用',
    creation_backoff: '创建已退避，等待重试', owned_cleanup_pending: '旧代理资源正在回收',
    hourly_creation_limit: '已达每小时创建上限', remote_create_failed: '远端代理创建失败',
    invalid_create_response: '远端创建响应无效', duplicate_client_id: '远端返回重复的代理标识',
    candidate_probe_failed: '候选代理检查失败', upstream_probe_failed: '上游健康检查失败',
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

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
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
    return parsed === null ? '—' : `${parsed.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${unit}`;
  }

  function dateText(value) {
    if (value === null || value === undefined || value === '') return '尚无记录';
    let input = value;
    if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) {
      input = Number(value) * (Number(value) < 1e12 ? 1000 : 1);
    }
    const date = new Date(input);
    return Number.isNaN(date.getTime()) ? '时间格式未知' : date.toLocaleString('zh-CN', { hour12: false });
  }

  function regionName(code, language = 'zh') {
    const normalized = String(code || '').toUpperCase();
    if (!/^[A-Z]{2}$/.test(normalized)) return normalized || '未指定';
    const names = language === 'en' ? englishNames : chineseNames;
    return names ? names.of(normalized) : normalized;
  }

  function countryName(country) {
    const local = regionName(country.code);
    return local === String(country.code).toUpperCase() ? country.name || local : local;
  }

  function countryMatches(country, query) {
    const haystack = [country.code, country.name, countryName(country), regionName(country.code, 'en')].join(' ').toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
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
        if (response.status === 401 && state.authenticated) endSession('会话已过期，请重新输入管理令牌。');
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
      try { await work(); } catch (error) {
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
    state.controllers.forEach((controller) => controller.abort());
    state.loads.clear();
    state.gateways = []; state.countries = []; state.rawCountries = []; state.events = []; state.after = 0;
    state.gatewaysReady = false; state.settingsReady = false; state.countryData = null;
    state.pending.clear(); state.errors.clear(); state.editing = null; state.deleting = null;
    state.gatewayRender = ''; state.savingGateway = false; state.savingSettings = false;
    document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
    ['gateway-list', 'country-list', 'event-list', 'recent-events', 'account-data', 'toasts'].forEach((id) => $(id).replaceChildren());
    document.querySelectorAll('.notice, .field-error').forEach((element) => { element.hidden = true; element.textContent = ''; });
    ['login-token', 'copy-value', 'gateway-name', 'gateway-search', 'country-search'].forEach((id) => { $(id).value = ''; });
    ['stat-total', 'stat-running', 'stat-countries', 'stat-connections', 'coverage-count', 'current-credential-type', 'api-base-url'].forEach((id) => { $(id).textContent = '—'; });
    document.querySelectorAll('[data-country-source], [data-country-updated]').forEach((element) => { element.textContent = '—'; });
    $('event-gateway').replaceChildren(new Option('全部网关', ''));
    state.configSnapshot = null; state.configDraft = {}; state.configDirty = false; state.configLoading = false;
    document.querySelectorAll('[data-config-field]').forEach((input) => { input.value = ''; });
    $('config-source').value = '';
    ['web-server-port', 'config-saved-port'].forEach((id) => { $(id).textContent = '—'; });
    updateCredentialLock();
    openLogin(reason);
  }

  async function connect(token, initial = false) {
    if (state.loggingIn) return;
    state.loggingIn = true;
    $('login-submit').disabled = true;
    $('login-token').disabled = true;
    $('login-status').textContent = '正在验证会话…';
    message('login-error', '');
    try {
      if (token) await api('/api/login', 'POST', { token });
      token = '';
      $('login-token').value = '';
      const session = await api('/api/session');
      if (typeof session.csrf !== 'string' || !session.csrf) throw new Error('会话缺少校验信息，请重新登录。');
      state.csrf = session.csrf;
      state.authenticated = true;
      $('workspace').inert = false;
      $('login-dialog').close();
      startPolling();
    } catch (error) {
      if (!isCancelled(error)) openLogin(initial && error.status === 401 ? '' : error.message);
    } finally {
      token = '';
      $('login-token').value = '';
      state.loggingIn = false;
      $('login-submit').disabled = false;
      $('login-token').disabled = false;
      $('login-status').textContent = '';
      if ($('login-dialog').open) $('login-token').focus();
    }
  }

  function startPolling() {
    state.timers.forEach(clearInterval);
    loadGateways(); loadEvents(); loadCountries(); loadAccount(); loadSettings(); loadHealth();
    state.timers = [
      setInterval(() => { loadGateways(); loadEvents(); }, POLL_MS),
      setInterval(() => { loadAccount(); loadHealth(); }, ACCOUNT_MS),
      setInterval(loadCountries, COUNTRIES_MS)
    ];
  }

  async function loadHealth() {
    return exclusive('health', async () => {
      try {
        const data = await api('/api/health');
        if (data.status !== 'ok') throw new Error('服务状态异常');
        $('health-state').textContent = '本地服务在线';
        $('health-state').className = 'health-state online';
      } catch (error) {
        if (!isCancelled(error)) {
          $('health-state').textContent = '本地服务未连接';
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
        $('gateway-sync').textContent = `已同步 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })} · 每 5 秒刷新`;
        renderGateways(); updateEventGatewayOptions(); updateCredentialLock();
      } catch (error) {
        state.gatewaysReady = false;
        updateCredentialLock();
        $('gateway-sync').textContent = '同步失败 · 保留上次数据，5 秒后重试';
        if (!state.gateways.length) renderEmpty($('gateway-list'), '暂未取得网关', '请确认本地服务在线，使用上方刷新重试。');
        throw error;
      } finally { $('gateway-list').setAttribute('aria-busy', 'false'); }
    });
  }

  function renderEmpty(container, title, description, action) {
    const empty = node('div', 'empty-state');
    empty.append(node('div', 'empty-symbol', '◇'), node('h3', '', title), node('p', '', description));
    if (action) {
      const button = node('button', 'button primary', '新建第一个网关');
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
    const gateways = state.gateways.filter(gatewayMatches);
    const running = state.gateways.filter((gateway) => gateway.enabled && gatewayStatus(gateway)[1] === 'good').length;
    $('stat-total').textContent = state.gateways.length;
    $('stat-running').textContent = running;
    $('stat-running-note').textContent = running ? '网关运行正常' : '暂无运行中的网关';
    $('stat-connections').textContent = amount(state.gateways.reduce((sum, gateway) => sum + (number(gateway.connections) || 0), 0));
    $('gateway-count').textContent = gateways.length;
    $('nav-gateway-count').textContent = state.gateways.length;
    const signature = JSON.stringify([gateways, [...state.pending], [...state.errors]]);
    if (signature === state.gatewayRender) return;
    state.gatewayRender = signature;
    if (!gateways.length) {
      renderEmpty($('gateway-list'), state.gateways.length ? '没有匹配的网关' : '从第一条连接开始', state.gateways.length ? '试试其他名称、国家、端口或状态筛选。' : '选择一个国家，为你的应用创建固定本地代理入口。', !state.gateways.length);
      return;
    }
    const focused = document.activeElement;
    const focusedId = focused.closest('[data-gateway-id]')?.dataset.gatewayId;
    const focusedAction = focused.dataset.action;
    $('gateway-list').replaceChildren(...gateways.map(gatewayCard));
    if (focusedId && focusedAction) {
      const card = [...$('gateway-list').children].find((item) => item.dataset.gatewayId === focusedId);
      const control = card && [...card.querySelectorAll('button')].find((item) => item.dataset.action === focusedAction);
      if (control && !control.disabled) control.focus({ preventScroll: true });
    }
  }

  function gatewayCard(gateway) {
    const busy = state.pending.has(String(gateway.id));
    const card = node('article', `gateway-card${busy ? ' pending' : ''}${gateway.last_error ? ' has-error' : ''}`);
    card.dataset.gatewayId = String(gateway.id); card.setAttribute('aria-busy', String(busy));
    const header = node('div', 'gateway-card-head');
    const identity = node('div', 'gateway-identity');
    const title = node('h3', '', gateway.name || '未命名网关'); title.title = gateway.name || '未命名网关';
    identity.append(title, node('p', '', gateway.mode === 'best' ? '自动选择 · 最佳节点' : `${regionName(gateway.country_code)} · 国家绑定`));
    const [label, tone] = gatewayStatus(gateway);
    const badge = node('span', `badge ${tone}`); badge.append(node('span', 'badge-dot'), document.createTextNode(label));
    header.append(node('span', `country-emblem${gateway.mode === 'best' ? ' auto' : ''}`, gateway.mode === 'best' ? '◎' : gateway.country_code || '—'), identity, badge);
    card.append(header, gatewayFacts(gateway), gatewayAddresses(gateway));
    const meta = node('div', 'gateway-meta');
    meta.append(node('span', '', gateway.auto_heal ? `↻ 自动恢复 · ${amount(gateway.interval, 's')}` : '自动恢复关闭'), node('span', '', `失败 ${amount(gateway.failures)} / ${amount(gateway.failures_before_rotate)}`), node('span', '', `换线 ${amount(gateway.rotations)} 次`));
    card.append(meta, node('p', 'gateway-last-check', `上次检查 ${dateText(gateway.last_check)}`));
    const error = eventMessage(state.errors.get(String(gateway.id)) || gateway.last_error);
    if (error) card.append(node('p', 'notice error', error));
    if (busy) card.append(node('p', 'field-hint', '正在执行操作，请稍候…'));
    card.append(gatewayActions(gateway, busy));
    return card;
  }

  function gatewayFacts(gateway) {
    const facts = node('dl', 'gateway-facts');
    [['出口 IP', gateway.exit_ip || '等待连接'], ['网络延迟', amount(gateway.latency_ms, ' ms')], ['活动连接', amount(gateway.connections)], ['本地端口', gateway.port]].forEach(([label, value]) => {
      const item = node('div'); item.append(node('dt', '', label), node('dd', '', value)); facts.append(item);
    });
    return facts;
  }

  function gatewayAddresses(gateway) {
    const container = node('div', 'gateway-addresses');
    const allowed = ['127.0.0.1', '::1', 'localhost'].includes(gateway.host);
    if (!allowed) { container.append(node('p', 'notice warning', '非回环监听，请在本地配置中核对地址。')); return container; }
    const host = gateway.host === '::1' ? '[::1]' : gateway.host;
    for (const [label, protocol] of [['HTTP', 'http'], ['SOCKS5', 'socks5']]) {
      const address = `${protocol}://${host}:${gateway.port}`;
      const row = node('div', 'address-row');
      const code = node('code', '', `${host}:${gateway.port}`); code.title = address;
      const button = node('button', 'copy-button', '复制'); button.type = 'button';
      button.dataset.action = `copy-${protocol}`;
      button.setAttribute('aria-label', `复制 ${gateway.name} 的 ${label} 地址`);
      button.addEventListener('click', () => copyAddress(address));
      row.append(node('span', 'address-kind', label), code, button); container.append(row);
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

  async function copyAddress(address) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('剪贴板不可用');
      await navigator.clipboard.writeText(address); toast('代理地址已复制');
    } catch {
      $('copy-value').value = address;
      if (!$('copy-dialog').open) $('copy-dialog').showModal();
      $('copy-value').focus(); $('copy-value').select();
    }
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
      await api(`/api/gateways/${encodeURIComponent(id)}/${action}`, 'POST', {});
      await refreshAfterMutation(); loadEvents();
      if (state.authenticated) toast({ start: '启动请求已完成', stop: '停止请求已完成', rotate: '换线请求已完成；新出口 IP 不保证变化', check: '健康检查已完成' }[action]);
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
        document.querySelectorAll('[data-country-stale]').forEach((element) => { element.hidden = false; element.textContent = '位置数据同步失败；已有快照仅供参考'; });
        if (!state.countryData) renderEmpty($('country-list'), '位置数据暂不可用', '请刷新重试；已创建的网关不受影响。');
        if ($('gateway-dialog').open) message('country-picker-error', error.message);
        throw error;
      } finally { $('country-list').setAttribute('aria-busy', 'false'); }
    });
  }

    function sortCountries(countries, sortKey = 'providers-desc') {
    const list = [...countries];
    switch (sortKey) {
      case 'providers-desc':
        return list.sort((a, b) => (b.providers - a.providers) || countryName(a).localeCompare(countryName(b), 'zh-CN'));
      case 'providers-asc':
        return list.sort((a, b) => (a.providers - b.providers) || countryName(a).localeCompare(countryName(b), 'zh-CN'));
      case 'name-asc':
        return list.sort((a, b) => countryName(a).localeCompare(countryName(b), 'zh-CN'));
      case 'code-asc':
        return list.sort((a, b) => a.code.localeCompare(b.code));
      default:
        return list.sort((a, b) => (b.providers - a.providers) || countryName(a).localeCompare(countryName(b), 'zh-CN'));
    }
  }

  function acceptCountries(data) {
    if (!Array.isArray(data.countries)) throw new Error('国家列表格式异常，请刷新重试。');
    state.countryData = data;
    state.rawCountries = data.countries;
    const sortVal = $('country-sort')?.value || 'providers-desc';
    state.countries = sortCountries(data.countries, sortVal);
    $('stat-countries').textContent = state.countries.length;
    $('coverage-count').textContent = state.countries.length;
    document.querySelectorAll('[data-country-source]').forEach((element) => { element.textContent = data.source || '未提供来源'; });
    document.querySelectorAll('[data-country-updated]').forEach((element) => { element.textContent = data.updated_at ? dateText(data.updated_at) : '未提供更新时间'; });
    document.querySelectorAll('[data-country-stale]').forEach((element) => {
      element.hidden = false;
      element.className = `notice ${data.stale || data.error ? 'warning' : 'positive'}`;
      element.textContent = data.stale ? '缓存已过期 · 请刷新核对' : data.error ? '刷新失败 · 当前为缓存快照' : '数据快照未过期';
    });
    message('countries-error', safeMessage(data.error));
    renderCountries();
    if ($('gateway-dialog').open) renderCountryOptions();
  }

  function renderCountries() {
    const sortVal = $('country-sort')?.value || 'providers-desc';
    const sourceList = state.rawCountries ? [...state.rawCountries] : [...state.countries];
    state.countries = sortCountries(sourceList, sortVal);
    const countries = state.countries.filter((country) => countryMatches(country, $('country-search').value));
    $('country-result-count').textContent = `显示 ${countries.length} / ${state.countries.length} 个国家与地区`;
    if (!countries.length) { renderEmpty($('country-list'), state.countries.length ? '没有匹配的国家' : '当前没有可用国家', '试试英文名称或国家代码，也可以刷新位置数据。'); return; }
    $('country-list').replaceChildren(...countries.map((country) => {
      const card = node('article', 'country-card');
      const heading = node('div', 'country-card-head');
      const name = node('div'); name.append(node('h3', '', countryName(country)), node('p', '', country.name || regionName(country.code, 'en')));
      heading.append(node('span', 'country-emblem', country.code), name);
      const providers = node('div', 'country-provider-count'); providers.append(node('span', '', '提供者'), node('strong', '', amount(country.providers)));
      const flags = node('div', 'country-flags');
      if (country.stable) flags.append(node('span', 'badge good', '稳定位置'));
      if (country.strong_privacy) flags.append(node('span', 'badge blue', '强隐私'));
      card.append(heading, providers, flags); return card;
    }));
  }

  function renderCountryOptions(preferred) {
    const selected = preferred === undefined ? $('gateway-country').value : preferred;
    const sortVal = $('gateway-country-sort')?.value || $('country-sort')?.value || 'providers-desc';
    let countries = state.rawCountries ? [...state.rawCountries] : [...state.countries];
    countries = sortCountries(countries, sortVal);
    countries = countries.filter((country) => countryMatches(country, $('gateway-country-search').value));
    const options = countries.map((country) => new Option(`${countryName(country)} · ${country.name || regionName(country.code, 'en')} (${country.code}) · ${amount(country.providers)} 提供者`, String(country.location_id)));
    const current = state.editing;
    if (current?.mode === 'country' && current.location_id && !state.countries.some((country) => String(country.location_id) === String(current.location_id)) && !$('gateway-country-search').value) {
      options.push(new Option(`${regionName(current.country_code)} (${current.country_code}) · 已保存绑定，当前快照未包含`, String(current.location_id)));
    }
    $('gateway-country').replaceChildren(new Option('请选择国家 / 地区', ''), ...options);
    $('gateway-country').value = options.some((option) => option.value === String(selected)) ? String(selected) : '';
    const data = state.countryData;
    $('country-picker-note').textContent = data ? `显示 ${countries.length} / ${state.countries.length} · ${data.source || '来源未提供'} · ${dateText(data.updated_at)}${data.stale ? ' · 缓存已过期' : ''}` : '位置数据尚未就绪；请稍候或刷新位置数据。';
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
    $('gateway-host').value = gateway?.host === '::1' ? '::1' : '127.0.0.1';
    const used = new Set(state.gateways.map((item) => Number(item.port)));
    let port = 1080; while (used.has(port) && port < 65535) port += 1;
    $('gateway-port').value = gateway?.port ?? port;
    $('gateway-mode').value = gateway?.mode || 'country';
    $('gateway-auto-heal').checked = gateway?.auto_heal ?? true;
    $('gateway-interval').value = gateway?.interval ?? 30;
    $('gateway-failures').value = gateway?.failures_before_rotate ?? 3;
    ['gateway-error', 'port-error', 'country-picker-error'].forEach((id) => message(id, ''));
    $('gateway-port').removeAttribute('aria-invalid');
    renderCountryOptions(gateway?.location_id || ''); syncGatewayMode();
    $('gateway-dialog').showModal(); $('gateway-name').focus();
    if (!state.countryData) loadCountries();
  }

  function syncGatewayMode() {
    const best = $('gateway-mode').value === 'best';
    $('country-picker').hidden = best;
    $('gateway-country').disabled = best;
    $('gateway-country-search').disabled = best;
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
    const host = $('gateway-host').value;
    if (!['127.0.0.1', '::1'].includes(host)) throw new Error('本期仅支持回环监听地址。');
    const interval = Number($('gateway-interval').value);
    const failures = Number($('gateway-failures').value);
    if (!Number.isInteger(interval) || interval < 5 || interval > 3600) throw new Error('检查间隔必须是 5–3600 秒的整数。');
    if (!Number.isInteger(failures) || failures < 1 || failures > 100) throw new Error('连续失败阈值必须是 1–100 的整数。');
    const mode = $('gateway-mode').value;
    let country = null;
    if (mode === 'country') {
      const selected = $('gateway-country').value;
      country = state.countries.find((item) => String(item.location_id) === selected);
      if (!country && selected && selected === String(state.editing?.location_id)) country = { code: state.editing.country_code, location_id: state.editing.location_id };
      if (!country || !selected) { message('country-picker-error', '请选择国家 / 地区；若列表为空，请刷新位置数据。'); $('gateway-country').focus(); return null; }
    }
    return { name, host, port, mode, country_code: country?.code || '', location_id: country?.location_id || '', auto_heal: $('gateway-auto-heal').checked, interval, failures_before_rotate: failures, enabled: state.editing?.enabled ?? true };
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
      await api(editing ? `/api/gateways/${encodeURIComponent(id)}` : '/api/gateways', editing ? 'PUT' : 'POST', payload);
      $('gateway-dialog').close();
      await refreshAfterMutation(); loadEvents();
      if (state.authenticated) toast(editing ? '网关设置已保存' : '网关已创建');
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
      catch (error) { $('account-data').replaceChildren(node('p', 'muted', '配额暂不可用，不影响本地网关操作。')); throw error; }
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
    details.append(node('p', '', data.network_name || '当前账户'), node('p', 'muted', `${data.is_guest ? '访客账户' : '已连接账户'} · ${data.plan_tier || '套餐未提供'}`), node('p', 'muted', `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`));
    $('account-data').replaceChildren(title, remaining, progress, values, details);
  }

  // Configuration drafts stay in memory; the server never returns existing secrets.
  function configInputs() { return [...document.querySelectorAll('[data-config-field]')]; }

  function writeConfigForm() {
    configInputs().forEach((input) => { input.value = state.configDraft[input.dataset.configField] ?? ''; });
  }

  function collectConfigForm() {
    // Only patch known fields; the authoritative JSONC source stays as text so
    // unknown integers/decimals never roundtrip through JavaScript Number.
    const draft = {};
    configInputs().forEach((input) => {
      const key = input.dataset.configField;
      draft[key] = input.type === 'number' && input.value !== '' ? Number(input.value) : input.value;
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
    message('config-restart', data.restart_required ? '文件配置已更新，重启服务后生效；当前连接仍使用启动时的配置。' : '', 'warning');
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
    $('credential-lock').textContent = state.gateways.length ? `当前有 ${state.gateways.length} 个网关：可保存一般参数；变更认证或 API 地址前，请删除网关并等待资源回收。` : '变更认证或 API 地址时，服务端会检查旧代理资源是否回收完成。';
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
      message('settings-success', '已保存到 config.json。配置在重启后生效，当前连接未中断。', 'positive');
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
        const events = new Map(state.events.map((event) => [Number(event.id), event]));
        for (const event of data.events) {
          const id = Number(event.id);
          if (!Number.isSafeInteger(id) || id < 0) continue;
          events.set(id, event); state.after = Math.max(state.after, id);
        }
        state.events = [...events.values()].sort((a, b) => Number(a.id) - Number(b.id)).slice(-EVENT_LIMIT);
        message('events-error', ''); message('recent-events-error', '');
        $('event-sync').textContent = `已同步 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })} · 每 5 秒更新`;
        updateEventGatewayOptions(); renderEvents();
      } catch (error) {
        if (!isCancelled(error) && state.authenticated) message('recent-events-error', error.message);
        $('event-sync').textContent = '同步失败 · 5 秒后自动重试'; throw error;
      } finally { $('event-list').setAttribute('aria-busy', 'false'); }
    });
  }

  function updateEventGatewayOptions() {
    const select = $('event-gateway');
    const selected = select.value;
    const gateways = new Map(state.gateways.map((gateway) => [String(gateway.id), gateway.name]));
    state.events.forEach((event) => { if (event.gateway_id && !gateways.has(String(event.gateway_id))) gateways.set(String(event.gateway_id), `已删除网关 · ${event.gateway_id}`); });
    select.replaceChildren(new Option('全部网关', ''), ...[...gateways].map(([id, name]) => new Option(name || id, id)));
    select.value = gateways.has(selected) ? selected : '';
  }

  function eventRow(event) {
    const level = event.level === 'warn' ? 'warning' : event.level;
    const row = node('div', 'event-row');
    const body = node('div', 'event-body');
    const details = node('div', 'event-details');
    const gateway = state.gateways.find((item) => String(item.id) === String(event.gateway_id));
    details.append(node('span', '', gateway?.name || (event.gateway_id ? `网关 ${event.gateway_id}` : '系统')), node('time', '', dateText(event.time)));
    body.append(node('p', 'event-message', eventMessage(event.message)), details);
    const tone = ['info', 'warning', 'error'].includes(level) ? level : 'info';
    row.append(node('span', `event-marker ${tone}`), body, node('span', 'event-level', { info: '信息', warning: '警告', error: '错误' }[level] || level || '信息'));
    return row;
  }

  function renderEvents() {
    const gateway = $('event-gateway').value;
    const level = $('event-level').value;
    const events = [...state.events].reverse();
    const filtered = events.filter((event) => (!gateway || String(event.gateway_id) === gateway) && (!level || (event.level === 'warn' ? 'warning' : event.level) === level));
    if (filtered.length) $('event-list').replaceChildren(...filtered.map(eventRow));
    else renderEmpty($('event-list'), '暂无匹配的事件', '网关启动、检查和换线后，事件会自动显示在这里。');
    if (events.length) $('recent-events').replaceChildren(...events.slice(0, 3).map(eventRow));
    else $('recent-events').replaceChildren(node('p', 'muted', '暂无动态。创建网关后，连接事件会出现在这里。'));
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
    if (view === 'events') loadEvents();
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
      confirmBtn.textContent = '正在深度清理…';
    }
    if (cancelBtn) cancelBtn.disabled = true;
    if (quickBtn) quickBtn.disabled = true;
    if (settingsBtn) settingsBtn.disabled = true;

    try {
      const res = await api('/api/proxies/clean', 'POST', {});
      $('clean-confirm-dialog').close();
      const count = res.proxies_removed ?? 0;
      const resetText = res.clients_reset ? '客户端连接已重置' : '未变动';
      toast(`深度清理完成！已移除 ${count} 个代理实例，${resetText}`);
      if ($('clean-status')) {
        $('clean-status').hidden = false;
        $('clean-status').className = 'notice positive';
        $('clean-status').textContent = `清理成功！已从 https://ur.io 网页端删除 ${count} 个代理实例，客户端会话计数重置: ${resetText}。刷新网页 Proxies 列表已清空。`;
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
        confirmBtn.textContent = '确认清空并重置';
      }
      if (cancelBtn) cancelBtn.disabled = false;
      if (quickBtn) quickBtn.disabled = false;
      if (settingsBtn) settingsBtn.disabled = false;
    }
  }

  function bindEvents() {
    document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
    document.querySelector('.brand').addEventListener('click', (event) => { event.preventDefault(); showView('gateways'); });
    $('login-form').addEventListener('submit', (event) => { event.preventDefault(); const token = $('login-token').value.trim(); if (token) connect(token); });
    $('login-dialog').addEventListener('cancel', (event) => event.preventDefault());
    $('logout').addEventListener('click', logout);
    $('add-gateway').addEventListener('click', () => openGateway());
    $('gateway-form').addEventListener('submit', saveGateway);
    ['gateway-close', 'gateway-cancel'].forEach((id) => $(id).addEventListener('click', () => { if (!state.savingGateway) $('gateway-dialog').close(); }));
    $('gateway-dialog').addEventListener('cancel', (event) => { if (state.savingGateway) event.preventDefault(); });
    $('gateway-mode').addEventListener('change', syncGatewayMode);
    $('gateway-country-search').addEventListener('input', () => renderCountryOptions());
    $('gateway-search').addEventListener('input', renderGateways);
    $('gateway-filter').addEventListener('change', renderGateways);
    $('country-search').addEventListener('input', renderCountries);
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
    document.addEventListener('visibilitychange', () => { if (!document.hidden && state.authenticated) { loadGateways(); loadEvents(); } });
  }

  // Consume and remove the launch secret before any network request.
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  let launchToken = fragment.get('token');
  if (fragment.has('token')) history.replaceState(history.state, '', window.location.pathname + window.location.search);
  fragment.delete('token');
  initTheme(); bindEvents(); openLogin();
  connect(launchToken, !launchToken);
  launchToken = null;
})();

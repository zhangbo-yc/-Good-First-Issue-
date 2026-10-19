'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const main = $('#main');
const state = { catalog: null, settings: { model_ready: false, model: '' }, mode: 'local', key: '', detail: null, routeToken: 0, controller: null, histories: new Map(), chatController: null, chatRun: null, chatTask: null, streaming: false, domain: '全部领域', projectQuery: '', lastQuestion: '', toastTimer: null };
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const txt = value => typeof value === 'string' ? value : value && typeof value === 'object' ? (value.text || value.description || value.title || value.name || value.stage || '') : '';
const routeProject = repo => `#/project/${encodeURIComponent(repo)}`;
const routeIssue = row => `#/issue/${encodeURIComponent(row.repo)}/${row.number}`;
const icon = name => {
  const shapes = { search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>', arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>', chevron: '<path d="m9 5 7 7-7 7"/>', external: '<path d="M14 4h6v6m0-6L10 14"/><path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>', book: '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15"/>', manim: '<path d="M3 19 9 5l4 10 3-7 5 11"/><circle cx="9" cy="5" r="2"/><path d="M3 19h18"/>', sympy: '<path d="M20 5H9l7 7-7 7h11M5 4v16"/>', napari: '<path d="m3 8 9-5 9 5-9 5-9-5Zm0 5 9 5 9-5M3 18l9 5 9-5"/>', dask: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h7v7h-7z"/>', ray: '<circle cx="5" cy="12" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="18" cy="19" r="2"/><path d="m7 11 9-5M7 13l9 5m-2-6h6"/>', code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>' };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${shapes[name] || shapes.code}</svg>`;
};
function safeURL(value) { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch { return ''; } }
function external(url, label, className = 'external-link') { const href = safeURL(url); return href ? `<a class="${className}" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}${icon('external')}</a>` : ''; }
function evidenceMarkup(sources) {
  return (sources || []).map(source => {
    const url = safeURL(source.url);
    return url ? `<a class="evidence-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer"><span>${esc(source.kind || '源码')}</span><strong>${esc(source.label)}</strong>${icon('external')}</a>` : '';
  }).join('');
}
function appendClaimMeta(target, label, sources = []) {
  if (!target) return;
  const meta = document.createElement('div');
  meta.className = 'claim-meta';
  if (label) {
    const badge = document.createElement('span');
    badge.className = `basis-badge ${label === '建议验证项' ? 'inferred' : ''}`;
    badge.textContent = label;
    meta.append(badge);
  }
  const links = evidenceMarkup(sources);
  if (links) {
    const list = document.createElement('div');
    list.className = 'evidence-link-list';
    list.innerHTML = links;
    meta.append(list);
  }
  if (meta.childElementCount) target.append(meta);
}
function addEvidencePresentation(detail) {
  if (!detail.has_analysis) return;
  const firstSection = $('.detail-content .content-section');
  if (firstSection) {
    const note = document.createElement('aside');
    note.className = 'analysis-origin';
    note.innerHTML = `<span class="analysis-origin-mark" aria-hidden="true">E</span><div><strong>${esc(detail.analysis_source || '证据导读')}</strong><p>这份导读结合任务描述与固定版本的项目资料整理。推断和建议会单独标明。</p></div>`;
    firstSection.after(note);
  }
  const goalSection = [...document.querySelectorAll('.detail-content .content-section')]
    .find(section => section.querySelector('h2')?.textContent === '需要达成的目标');
  if (goalSection && detail.goal_basis) {
    appendClaimMeta(goalSection, detail.goal_basis, detail.goal_sources);
  }
  const stageSection = [...document.querySelectorAll('.detail-content .content-section')]
    .find(section => section.querySelector('h2')?.textContent === '什么基础更容易开始');
  if (stageSection && detail.stage?.sources?.length) {
    appendClaimMeta(stageSection, '依据任务证据估计', detail.stage.sources);
  }
  document.querySelectorAll('.purpose-item').forEach((item, index) => {
    const requirement = (detail.requirements || [])[index];
    if (requirement) appendClaimMeta(item, requirement.basis_label, requirement.sources);
  });
  const acceptanceSection = [...document.querySelectorAll('.detail-content .content-section')]
    .find(section => section.querySelector('h2')?.textContent === '如何验证这次贡献');
  if (acceptanceSection) {
    acceptanceSection.querySelectorAll('li').forEach((item, index) => {
      const claim = (detail.acceptance_claims || [])[index];
      if (claim) appendClaimMeta(item, claim.basis_label, claim.sources);
    });
  }
  const sourceList = evidenceMarkup(detail.evidence_sources);
  if (sourceList) {
    const section = document.createElement('section');
    section.className = 'content-section evidence-section';
    section.innerHTML = '<h2>可追溯的源码与测试依据</h2><p class="evidence-intro">打开文件位置查看导读使用的固定仓库材料。</p><div class="evidence-link-list"></div>';
    section.querySelector('.evidence-link-list').innerHTML = sourceList;
    $('.detail-content')?.append(section);
  }
}
function tags(items, style = '') { return (items || []).map(t => `<span class="tag ${style}">${esc(typeof t === 'string' ? t : t.name)}</span>`).join(''); }
function projectMark(project) { return `<span class="project-icon">${icon(project.icon)}</span>`; }
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3000); }
async function api(path, signal) { const response = await fetch(path, { signal, headers: { Accept: 'application/json' } }); const data = await response.json(); if (!response.ok) throw new Error(data.message || '读取内容失败，请重试。'); return data; }
function errorView(error) { main.innerHTML = `<section class="empty-state"><h2>内容暂时没有加载成功</h2><p>${esc(error.message || '请检查连接后重试。')}</p><button class="button primary" id="route-retry">重新加载</button></section>`; $('#route-retry').onclick = renderRoute; }
function emptyView(title, description) { return `<section class="empty-state"><h2>${esc(title)}</h2><p>${esc(description)}</p><button type="button" class="button secondary" data-reset-filters>重置筛选</button></section>`; }
function breadcrumbs(project, issue = false) { return `<nav class="breadcrumbs" aria-label="面包屑"><a href="#/projects">发现项目</a>${icon('chevron')}${issue ? `<a href="${routeProject(project.repo)}">${esc(project.name)}</a>${icon('chevron')}<span>任务导读</span>` : `<span>${esc(project.name)}</span>`}</nav>`; }
function projectCard(project) {
  return `<article class="project-card"><div class="project-top"><div class="project-identity">${projectMark(project)}<div><a class="project-name" href="${routeProject(project.repo)}">${esc(project.name)}</a><div class="project-repo">${esc(project.repo)}</div></div><span class="domain-label">${esc(project.domain)}</span></div><h2 class="project-tagline">${esc(project.tagline)}</h2><p class="project-description">${esc(project.description)}</p><div class="tags">${tags(project.topics)}</div><div class="project-counts"><span><strong>${esc(project.task_count)}</strong>个任务</span><span><strong>${esc(project.guide_count)}</strong>份指引</span><span><strong>${esc(project.analysis_count)}</strong>篇导读</span></div></div><div class="preview-block"><div class="preview-heading"><span>从这些任务开始了解</span><span>任务预览</span></div>${(project.previews || []).map(row => `<a class="preview-link" href="${routeIssue(row)}"><span>${esc(row.title)}</span></a>`).join('')}</div><div class="project-footer"><span>${esc(project.language)} 项目</span><a href="${routeProject(project.repo)}">探索项目 ${icon('arrow')}</a></div></article>`;
}
const methodScores = [
  { id: 'A', name: 'Issue', correctness: 3.41, completeness: 2.54, actionability: 2.23, guidance: 3.05 },
  { id: 'B', name: '固定检索', correctness: 3.46, completeness: 3.03, actionability: 2.62, guidance: 3.69 },
  { id: 'C', name: '证据核查', correctness: 3.18, completeness: 2.62, actionability: 2.44, guidance: 3.62 },
  { id: 'D', name: '自适应补检', correctness: 3.56, completeness: 3.13, actionability: 2.92, guidance: 3.85 }
];

function renderMethod() {
  document.title = '研究方法与实验 · 开源起点';
  const score = value => `<span class="score-track" aria-label="${value}分，满分4分"><span style="--score:${Math.max(0, Math.min(100, value / 4 * 100))}%"></span></span><strong>${value.toFixed(2)}</strong>`;
  main.innerHTML = `<nav class="breadcrumbs" aria-label="面包屑"><a href="#/projects">发现项目</a>${icon('chevron')}<span>研究方法</span></nav><section class="method-hero"><div class="method-hero-copy"><span class="method-kicker">科研软件任务理解</span><h1>让任务解释能回到具体证据</h1><p>研究源码检索、逐条核查与自适应补检如何影响 Issue 的正确理解、内容覆盖和新人入手指导。</p></div><div class="method-sample"><strong>39</strong><span>留出任务</span><i></i><strong>156</strong><span>A/B/C/D答案</span><small>5个科研软件开源项目</small></div></section><section class="method-flow" aria-labelledby="method-flow-title"><div class="section-heading"><div><span class="section-kicker">分析路径</span><h2 id="method-flow-title">从任务描述到可追溯的入手建议</h2></div><p>模型每一步都围绕同一份固定版本源码证据。</p></div><div class="method-steps"><article><span class="step-mark">01</span><h3>读取 Issue</h3><p>提取用户报告的现象、目标和复现线索。</p></article><article><span class="step-mark">02</span><h3>检索源码与测试</h3><p>从固定仓库快照取出相关片段，保留文件路径和行号。</p></article><article><span class="step-mark">03</span><h3>逐项核查</h3><p>把结论标为材料明示、依据证据推断，或仍需确认。</p></article><article><span class="step-mark">04</span><h3>针对缺口补检</h3><p>D组继续寻找缺失的实现或测试依据，最多两轮。</p></article></div></section><section class="method-ablation" aria-labelledby="ablation-title"><div class="section-heading"><div><span class="section-kicker">消融比较</span><h2 id="ablation-title">逐步增加证据处理能力</h2></div><p>A/B/C/D使用同一任务分析结构。</p></div><div class="ablation-grid"><article><b>A</b><strong>Issue基线</strong><span>只读Issue</span></article><article><b>B</b><strong>固定检索</strong><span>Issue + 源码/测试</span></article><article><b>C</b><strong>证据核查</strong><span>逐条筛查结论</span></article><article><b>D</b><strong>自适应补检</strong><span>针对缺口再检索</span></article></div></section><section class="method-results" aria-labelledby="method-results-title"><div class="section-heading"><div><span class="section-kicker">探索性结果</span><h2 id="method-results-title">模型辅助评分（0–4分）</h2></div><p>DeepSeek Pro评审39个留出任务；这不是人工金标准或准确率。</p></div><div class="results-table-wrap"><table class="results-table"><thead><tr><th scope="col">条件</th><th scope="col">正确性</th><th scope="col">完整性</th><th scope="col">主答案可操作性</th><th scope="col">入手指导性</th></tr></thead><tbody>${methodScores.map(row => `<tr class="${row.id === 'D' ? 'method-best' : ''}"><th scope="row"><span class="condition-mark">${row.id}</span><span>${row.name}</span></th><td>${score(row.correctness)}</td><td>${score(row.completeness)}</td><td>${score(row.actionability)}</td><td>${score(row.guidance)}</td></tr>`).join('')}</tbody></table></div><div class="result-reading"><span class="result-rule" aria-hidden="true"></span><p>D组四项均分最高；C组低于B组，提示核查若过滤过严可能损失有用内容。等级分来自单一模型评审，按探索性信号解读。</p></div></section><section class="method-limits"><strong>实验边界</strong><p>Issue按固定快照任务理解，不推断当前认领状态；模型评分不等同人工核验。能力门槛专项复核未覆盖全部任务，未展示该指标。</p></section><a class="method-back-link" href="#/projects">返回科研软件项目 <span aria-hidden="true">↗</span></a>`;
  $('.method-sample').innerHTML = '<strong>39</strong><span>独立 Issue</span><b aria-hidden="true">× 4 =</b><strong>156</strong><span>实验答案</span><small>四组逐题对照，不是156个不同 Issue</small>';
  const volume = document.createElement('section');
  volume.className = 'method-volume';
  volume.innerHTML = '<div class="volume-heading"><div><span class="section-kicker">数据与工作量</span><h2>156份结果，来自39个逐题对照</h2></div><p>每个留出 Issue 都运行 A、B、C、D 四种条件，并完成模型辅助评分。</p></div><div class="volume-projects"><span>Manim <b>8</b></span><span>SymPy <b>9</b></span><span>Dask <b>9</b></span><span>Ray <b>9</b></span><span>napari <b>4</b></span></div><p class="volume-corpus"><strong>语料规模</strong> 五个项目共采集967条 Issue 记录，清洗得到945个具体任务和22份贡献指引。冻结评测集原有45项，排除6个试点/调试任务后，剩余39项用于留出比较。</p>';
  $('.method-flow').before(volume);
}

function renderCatalog() {
  const total = state.catalog.totals;
  document.title = '发现项目 · 开源起点';
  main.innerHTML = `<section class="page-intro"><div><h1>从 Issue 找到科研软件任务的代码入口</h1><p>先看清问题，再沿着源码和测试证据理解任务、规划入手步骤。</p></div><div class="intro-aside"><strong>${esc(total.projects)}</strong> 个项目，<strong>${esc(total.tasks)}</strong> 个任务等你探索</div></section><section class="research-callout"><div class="callout-copy"><span>研究主线</span><strong>证据核查与自适应检索</strong><p>每篇导读连接任务描述与固定版本的源码、测试；依据不足时再针对缺口补检。</p></div><a href="#/method">查看方法与实验 <span aria-hidden="true">↗</span></a></section><div class="catalog-bar"><div class="domain-filters" role="group" aria-label="项目领域">${['全部领域', ...new Set(state.catalog.projects.map(p => p.domain))].map(domain => `<button class="pill-button ${domain === state.domain ? 'selected' : ''}" data-domain="${esc(domain)}" aria-pressed="${domain === state.domain}">${esc(domain)}</button>`).join('')}</div><label class="search-field">${icon('search')}<span class="sr-only">搜索项目、方向或技术</span><input id="project-search" type="search" placeholder="搜索项目、方向或技术" value="${esc(state.projectQuery)}"></label></div><div id="project-grid" class="project-grid"></div><aside class="learning-tip">${icon('book')}<p><strong>不确定从哪里开始？</strong>先选一个你感兴趣的项目，优先阅读「详细导读」任务，了解需要哪些基础、代码在何处，以及如何开始。</p></aside>`;
  $('.callout-copy').insertAdjacentHTML('beforeend', '<div class="callout-workload"><strong>39 个留出 Issue × 4 组 = 156 份实验答案</strong><span>查看样本分布、消融设计和模型评审结果</span></div>');
  const paint = () => {
    const query = state.projectQuery.trim().toLocaleLowerCase();
    const projects = state.catalog.projects.filter(p => (state.domain === '全部领域' || p.domain === state.domain) && [p.name, p.repo, p.domain, p.description, ...(p.topics || [])].join(' ').toLocaleLowerCase().includes(query));
    $('#project-grid').innerHTML = projects.length ? projects.map(projectCard).join('') : emptyView('没有找到匹配的项目', '尝试其他关键词，或查看全部领域。');
    $('[data-reset-filters]')?.addEventListener('click', () => { state.domain = '全部领域'; state.projectQuery = ''; renderCatalog(); });
  };
  $('[data-domain]')?.closest('.domain-filters').addEventListener('click', event => { const button = event.target.closest('[data-domain]'); if (button) { state.domain = button.dataset.domain; renderCatalog(); } });
  $('#project-search').addEventListener('input', event => { state.projectQuery = event.target.value; paint(); });
  paint();
}
function taskCard(row) {
  return `<article class="task-card"><div class="task-card-top"><span>${esc(row.project_name)} <span aria-hidden="true">/</span> #${esc(row.number)}</span><span class="tag ${row.has_analysis ? 'teal' : ''}">${row.has_analysis ? '详细导读' : row.kind === 'guide' ? '贡献指引' : '任务概要'}</span></div><h3><a href="${routeIssue(row)}">${esc(row.title)}</a></h3><p class="task-card-description">${esc(row.description)}</p><div class="task-card-bottom"><div class="tags">${tags((row.category_labels || []).slice(0, 2))}${tags((row.technologies || []).slice(0, 2), 'purple')}</div><a href="${routeIssue(row)}">阅读${row.kind === 'guide' ? '指引' : '任务'} ${icon('arrow')}</a></div></article>`;
}
function listRoute(repo, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== '' && value !== false && value != null && !(key === 'page' && Number(value) === 1)) query.set(key, value);
  return `${repo ? routeProject(repo) : '#/tasks'}${query.size ? '?' + query.toString() : ''}`;
}
function filterValues(params) { return { q: params.get('q') || '', kind: ['task', 'guide', 'all'].includes(params.get('kind')) ? params.get('kind') : 'task', label: params.get('label') || '', ready: params.get('ready') === 'true', page: Math.max(1, Number(params.get('page')) || 1) }; }
async function renderList(repo, params, token, signal) {
  const project = repo ? state.catalog.projects.find(p => p.repo === repo) : null;
  if (repo && !project) throw new Error('没有找到这个项目，请返回项目广场重新选择。');
  const filters = filterValues(params);
  document.title = `${project ? project.name : '全部任务'} · 开源起点`;
  const types = project ? project.type_filters : [...new Map(state.catalog.projects.flatMap(p => p.type_filters).map(t => [t.id, t])).values()];
  const projectOptions = state.catalog.projects.map(p => `<option value="${esc(p.repo)}">${esc(p.name)}</option>`).join('');
  main.innerHTML = `${project ? breadcrumbs(project) + `<section class="project-banner"><div><div class="banner-title">${projectMark(project)}<h1>${esc(project.name)}</h1><span class="domain-label">${esc(project.domain)}</span></div><p class="banner-description">${esc(project.description)}</p><div class="banner-links">${external(project.url, '项目仓库')}${(project.reading_links || []).map(l => external(l.url, l.label)).join('')}</div></div><div class="banner-stat"><strong>${esc(project.task_count)}</strong>个收录任务</div></section><div class="project-learning"><p><strong>适合的基础</strong>${esc(project.background)}</p><p><strong>可以学到</strong>${esc(project.learning)}</p></div>` : `<section class="page-intro"><div><h1>从一个具体任务开始</h1><p>浏览 ${esc(state.catalog.totals.tasks)} 个任务与 ${esc(state.catalog.totals.guides)} 份贡献指引，找到你感兴趣的问题。</p></div></section>`}<section class="filter-panel" aria-label="任务筛选"><div class="filter-row"><div class="type-tabs" role="group" aria-label="内容类型">${[['task', '具体任务'], ['guide', '贡献指引'], ['all', '全部内容']].map(([kind, name]) => `<button data-kind="${kind}" class="${kind === filters.kind ? 'selected' : ''}" aria-pressed="${kind === filters.kind}">${name}</button>`).join('')}</div><label class="search-field">${icon('search')}<span class="sr-only">搜索中文任务、技术或编号</span><input id="task-search" type="search" value="${esc(filters.q)}" placeholder="搜索中文任务、技术或编号"></label></div><div class="filter-row">${!project ? `<label class="sr-only" for="project-filter">选择项目</label><select id="project-filter" class="filter-select"><option value="">所有项目</option>${projectOptions}</select>` : ''}<label class="sr-only" for="type-filter">任务类型</label><select id="type-filter" class="filter-select"><option value="">所有任务类型</option>${types.map(type => `<option value="${esc(type.id)}" ${type.id === filters.label ? 'selected' : ''}>${esc(type.name)}</option>`).join('')}</select><label class="checkbox-label"><input id="ready-filter" type="checkbox" ${filters.ready ? 'checked' : ''}>只看有详细导读的任务</label><button class="text-button" id="reset-filters">重置筛选</button></div></section><div class="list-heading"><h2>${filters.kind === 'guide' ? '贡献指引' : filters.kind === 'all' ? '全部内容' : '项目任务'}</h2><span id="list-count" role="status">正在加载…</span></div><div class="task-grid" id="task-grid"><div class="loading" role="status">正在查找任务…</div></div><div id="pagination"></div>`;
  let searchTimer;
  const update = changes => { clearTimeout(searchTimer); location.hash = listRoute(repo, { ...filters, page: 1, ...changes }); };
  $('#task-search').oninput = event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (token === state.routeToken) update({ q: event.target.value }); }, 400); };
  $('#task-search').onkeydown = event => { if (event.key === 'Enter') update({ q: event.target.value }); };
  $('.type-tabs').onclick = event => { const button = event.target.closest('[data-kind]'); if (button) update({ kind: button.dataset.kind }); };
  $('#type-filter').onchange = event => update({ label: event.target.value });
  $('#ready-filter').onchange = event => update({ ready: event.target.checked });
  $('#project-filter')?.addEventListener('change', event => { location.hash = listRoute(event.target.value, { ...filters, page: 1 }); });
  $('#reset-filters').onclick = () => { location.hash = listRoute(repo); };
  const query = new URLSearchParams({ project: repo || '', q: filters.q, kind: filters.kind, ready: String(filters.ready), label: filters.label, page: String(filters.page), page_size: '12' });
  const result = await api('/api/issues?' + query, signal);
  if (token !== state.routeToken) return;
  $('#list-count').textContent = `共 ${result.total} 条 · 第 ${result.page} / ${result.pages} 页`;
  $('#task-grid').innerHTML = result.items.length ? result.items.map(taskCard).join('') : emptyView('暂时没有符合条件的内容', '减少筛选条件，或换一个关键词再试。');
  $('[data-reset-filters]')?.addEventListener('click', () => { location.hash = listRoute(repo); });
  $('#pagination').innerHTML = result.pages > 1 ? `<nav class="pagination" aria-label="列表分页"><button id="page-prev" ${result.page <= 1 ? 'disabled' : ''}>上一页</button><span>${result.page} / ${result.pages}</span><button id="page-next" ${result.page >= result.pages ? 'disabled' : ''}>下一页</button></nav>` : '';
  $('#page-prev')?.addEventListener('click', () => { location.hash = listRoute(repo, { ...filters, page: result.page - 1 }); });
  $('#page-next')?.addEventListener('click', () => { location.hash = listRoute(repo, { ...filters, page: result.page + 1 }); });
}
function listSection(title, values, ordered = false) { const rows = (values || []).map(txt).filter(Boolean); return rows.length ? `<section class="content-section"><h2>${esc(title)}</h2><${ordered ? 'ol' : 'ul'} class="content-list">${rows.map(value => `<li>${esc(value)}</li>`).join('')}</${ordered ? 'ol' : 'ul'}></section>` : ''; }
function recommendation(row) { return `<article class="recommendation-card"><div><span class="tag">#${esc(row.number)}</span></div><h3><a href="${routeIssue(row)}">${esc(row.title)}</a></h3><p>${esc(row.reason)}</p><div class="tags">${tags(row.shared, 'purple')}</div><p class="new-learning">${esc(row.new_learning)}</p><a href="${routeIssue(row)}">阅读相关任务</a></article>`; }
async function renderDetail(repo, number, token, signal) {
  const detail = await api('/api/issue?' + new URLSearchParams({ project: repo, number }), signal);
  if (token !== state.routeToken) return;
  state.detail = detail;
  document.title = `${detail.title} · 开源起点`;
  const stage = txt(detail.stage);
  const stageRationale = typeof detail.stage === 'object' && detail.stage ? txt(detail.stage.rationale) : '';
  const entryConditions = typeof detail.stage === 'object' && detail.stage ? detail.stage.entry_conditions : [];
  const conditionRows = (Array.isArray(entryConditions) ? entryConditions : [entryConditions]).map(txt).filter(Boolean);
  const codeLinks = (detail.code_links || []).map(link => `<div>${external(link.url, link.path, 'code-link')}<small>${link.kind === 'test' ? '测试阅读入口' : '实现阅读入口'}</small></div>`).join('');
  main.innerHTML = `${breadcrumbs(detail.project, true)}<header class="detail-heading"><div><h1>${esc(detail.title)}</h1><div class="detail-meta"><a href="${routeProject(repo)}">${esc(detail.project.name)}</a><span>#${esc(detail.number)}</span><span class="tag ${detail.has_analysis ? 'teal' : ''}">${detail.has_analysis ? '详细导读' : detail.kind === 'guide' ? '贡献指引' : '任务概要'}</span>${tags(detail.category_labels)}</div></div><button class="button primary" id="open-assistant"><span aria-hidden="true">✳</span>向助手提问</button></header><div class="detail-grid"><article class="detail-content"><section class="content-section"><h2>${detail.kind === 'guide' ? '这份指引在讲什么' : '这个任务在做什么'}</h2><p class="prose">${esc(detail.description)}</p></section>${detail.has_analysis ? `${detail.goal && detail.goal !== detail.description ? `<section class="content-section"><h2>需要达成的目标</h2><p class="prose">${esc(detail.goal)}</p></section>` : ''}${detail.requirements.length ? `<section class="content-section"><h2>需要哪些技术，以及为什么</h2><div class="purpose-list">${detail.requirements.map(item => `<div class="purpose-item"><strong>${esc(item.name)}</strong><p>${esc(item.purpose || '具体用途尚待进一步确认。')}</p></div>`).join('')}</div></section>` : ''}${detail.background || stage || stageRationale || conditionRows.length ? `<section class="content-section"><h2>什么基础更容易开始</h2>${detail.background ? `<p class="prose">${esc(detail.background)}</p>` : ''}${stage ? `<div class="tags"><span class="tag purple">${esc(stage)}</span></div>` : ''}${stageRationale ? `<p class="stage-rationale">${esc(stageRationale)}</p>` : ''}${conditionRows.length ? `<div class="entry-conditions"><h3>开始前，确认这些基础</h3><ul class="content-list">${conditionRows.map(condition => `<li>${esc(condition)}</li>`).join('')}</ul></div>` : ''}</section>` : ''}${listSection('建议的开始步骤', detail.steps, true)}${listSection('如何验证这次贡献', detail.acceptance)}${listSection('开始前还要确认', detail.unknowns)}` : `<section class="content-section"><div class="subtle-notice">这条内容还没有详细技术导读。当前可以阅读中文概要与项目贡献指引；具体涉及的技术、实施步骤和验收方式仍需结合实现确认。</div></section>`}</article><aside class="detail-side"><section class="side-card assistant-prompt"><span class="assistant-symbol" aria-hidden="true">✳</span><h2>读到这里，有个问题？</h2><p>围绕当前任务，问问需要的基础、如何开始，或下一步值得探索什么。</p><button class="button primary" id="side-assistant">打开任务助手</button></section><section class="side-card"><h2>项目与贡献入口</h2><div class="code-links">${external(detail.url, '查看原始任务')}${external(detail.project.url, '浏览项目仓库')}${(detail.project.reading_links || []).map(link => external(link.url, link.label)).join('')}</div></section>${codeLinks ? `<section class="side-card"><h2>从代码与测试读起</h2><div class="code-links">${codeLinks}</div></section>` : detail.source_search_url ? `<section class="side-card"><h2>查找相关实现</h2><p>尚未定位具体代码文件，可以先用任务关键词搜索项目源码。</p><div class="source-search">${external(detail.source_search_url, '搜索相关源码')}</div></section>` : ''}</aside></div>${detail.has_analysis && detail.recommendations.length ? `<section class="recommendations"><h2>沿着这些技术继续探索</h2><p>以下是同项目中的相关任务。共同技术帮助你建立联系，新增知识提示你可能接触的内容。</p><div class="recommendation-grid">${detail.recommendations.slice(0, 3).map(recommendation).join('')}</div></section>` : `<section class="recommendations"><h2>继续了解 ${esc(detail.project.name)}</h2><p>${detail.has_analysis ? '当前没有可展示的技术关联推荐，可以继续浏览项目中的其他任务。' : '详细分析完成前，先从项目的其他任务了解更多场景。'}</p><a class="button secondary" href="${routeProject(repo)}">浏览同项目任务 ${icon('arrow')}</a></section>`}`;
  addEvidencePresentation(detail);
  $('#open-assistant').onclick = openAssistant;
  $('#side-assistant').onclick = openAssistant;
}
async function renderRoute() {
  const previous = state.detail?.task_id;
  state.controller?.abort();
  state.controller = new AbortController();
  const token = ++state.routeToken;
  state.detail = null;
  closeAssistant();
  main.setAttribute('aria-busy', 'true');
  const hash = location.hash.slice(1) || '/projects';
  const [path, search = ''] = hash.split('?');
  const parts = path.split('/').filter(Boolean);
  const type = parts[0] || 'projects';
  document.querySelectorAll('[data-nav]').forEach(link => { const active = link.dataset.nav === (type === 'tasks' ? 'tasks' : type === 'method' ? 'method' : 'projects'); link.classList.toggle('active', active); if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
  main.innerHTML = '<div class="loading" role="status">正在加载内容…</div>';
  try {
    if (!state.catalog) state.catalog = await api('/api/catalog', state.controller.signal);
    if (token !== state.routeToken) return;
    if (type === 'projects') renderCatalog();
    else if (type === 'method') renderMethod();
    else if (type === 'tasks') await renderList(null, new URLSearchParams(search), token, state.controller.signal);
    else if (type === 'project' && parts[1]) await renderList(decodeURIComponent(parts[1]), new URLSearchParams(search), token, state.controller.signal);
    else if (type === 'issue' && parts[1] && /^\d+$/.test(parts[2])) await renderDetail(decodeURIComponent(parts[1]), Number(parts[2]), token, state.controller.signal);
    else throw new Error('页面不存在，请通过顶部导航选择项目或任务。');
    if (token === state.routeToken) { window.scrollTo(0, 0); if (previous) main.focus({ preventScroll: true }); }
  } catch (error) { if (error.name !== 'AbortError' && token === state.routeToken) errorView(error); }
  finally { if (token === state.routeToken) main.removeAttribute('aria-busy'); }
}

function updateConnection() {
  const connected = state.settings.model_ready || !!state.key;
  $('#connection-label').textContent = state.mode === 'model' && connected ? '模型助手已配置' : '资料助手';
  $('.connection-dot').classList.toggle('connected', state.mode === 'model' && connected);
  $('#assistant-mode-label').textContent = state.mode === 'model' ? '模型助手 · 根据任务上下文回答' : '资料助手 · 整理已有任务材料';
}
function openSettings() {
  $(`input[name="mode"][value="${state.mode}"]`).checked = true;
  $('#api-key').value = state.key;
  $('#server-status').textContent = state.settings.model_ready ? `服务器已配置 ${state.settings.model || '模型'}，可直接使用模型助手。` : '服务器尚未配置模型。可填入会话密钥，或使用资料助手。';
  $('#settings-message').textContent = '';
  $('#settings-dialog').showModal();
}
function closeSettings() { $('#api-key').value = ''; $('#settings-dialog').close(); }
$('#settings-open').onclick = openSettings;
$('[data-close-settings]').onclick = closeSettings;
$('#settings-dialog').addEventListener('cancel', () => { $('#api-key').value = ''; });
$('#settings-form').onsubmit = event => {
  event.preventDefault();
  const mode = $('input[name="mode"]:checked').value;
  const key = $('#api-key').value.trim();
  if (mode === 'model' && !key && !state.settings.model_ready) { $('#settings-message').textContent = '请填写会话密钥，或选择资料助手。'; return; }
  if (state.streaming) cancelChat();
  state.mode = mode; state.key = key; updateConnection(); closeSettings(); toast('助手设置已保存');
};
$('#forget-key').onclick = () => { state.key = ''; $('#api-key').value = ''; if (!state.settings.model_ready) { state.mode = 'local'; $('input[name="mode"][value="local"]').checked = true; } updateConnection(); $('#settings-message').textContent = '已清除当前会话密钥。'; };

// Only this small Markdown subset is supported. All content is escaped before markup.
function inlineMarkdown(value) {
  const pieces = String(value).split(/(`[^`\n]+`|\[[^\]\n]+\]\(https:\/\/[^\s)]+\)|\*\*[^*\n]+\*\*)/g);
  return pieces.map(piece => {
    if (piece.startsWith('`') && piece.endsWith('`')) return `<code>${esc(piece.slice(1, -1))}</code>`;
    if (piece.startsWith('**') && piece.endsWith('**')) return `<strong>${esc(piece.slice(2, -2))}</strong>`;
    const link = piece.match(/^\[([^\]]+)\]\((https:\/\/[^\s)]+)\)$/);
    if (link) { const url = safeURL(link[2]); return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(link[1])}</a>` : esc(link[1]); }
    return esc(piece);
  }).join('');
}
function markdown(value) {
  return String(value).split(/(```[\s\S]*?```)/g).map(block => {
    if (block.startsWith('```') && block.endsWith('```')) { const inner = block.slice(3, -3).replace(/^[a-zA-Z0-9_-]*\n/, ''); return `<pre><code>${esc(inner)}</code></pre>`; }
    return block.split(/\n{2,}/).filter(Boolean).map(paragraph => `<p>${inlineMarkdown(paragraph).replace(/\n/g, '<br>')}</p>`).join('');
  }).join('');
}
function failReply(reply, message) { reply.text = ''; reply.status = 'failed'; reply.error = message; }
function chatHistory(messages) { return messages.filter(message => message.text.trim() && (message.role === 'user' || message.status === 'complete')).slice(-8).map(message => ({ role: message.role, text: message.text })); }
function historyFor(taskId) { if (!state.histories.has(taskId)) state.histories.set(taskId, []); return state.histories.get(taskId); }
function renderChat() {
  const messages = historyFor(state.chatTask);
  const log = $('#chat-log');
  const nearEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 100;
  log.innerHTML = messages.length ? messages.map((message, index) => `<div class="chat-message ${message.role}" ${message.status === 'pending' ? 'data-streaming="true"' : ''}><div class="chat-message-label">${message.role === 'user' ? '你' : '任务助手'}</div><div class="chat-body">${message.role === 'user' ? esc(message.text).replace(/\n/g, '<br>') : message.status === 'failed' ? `<div class="chat-failure"><p>${esc(message.error || '这次回答未完成，请重试。')}</p><button type="button" class="text-button" data-retry-message="${index}" ${state.streaming ? 'disabled' : ''}>重新回答这个问题</button></div>` : markdown(message.text || '正在阅读任务材料…')}</div></div>`).join('') : `<div class="chat-welcome"><strong>一起把这个任务读明白</strong>${state.mode === 'model' ? '可以围绕当前任务自由提问。助手会参考已有导读与相关代码材料。' : '当前使用资料助手，回答来自已有任务材料。你可以询问任务目标、所需基础或阅读顺序。'}</div>`;
  if (nearEnd || messages.length < 3) log.scrollTop = log.scrollHeight;
  $('#chat-send').disabled = state.streaming;
  $('#chat-cancel').hidden = !state.streaming;
  $('#chat-clear').disabled = state.streaming || !messages.length;
  $('#chat-retry').hidden = state.streaming || !messages.some(message => message.role === 'user');
  $('#chat-suggestions').querySelectorAll('button').forEach(button => { button.disabled = state.streaming; });
}
function openAssistant() {
  if (!state.detail) return;
  state.chatTask = state.detail.task_id;
  $('#assistant-task-title').textContent = `${state.detail.title} · #${state.detail.number}`;
  $('#chat-error').textContent = '';
  $('#chat-question').value = '';
  updateConnection(); renderChat();
  $('#assistant-dialog').showModal();
  $('#chat-question').focus();
}
function cancelChat() {
  const run = state.chatRun;
  if (!run) return;
  failReply(run.reply, '回答已停止，未完成的内容已清除。可以重新回答这个问题。');
  state.chatRun = null; state.chatController = null; state.streaming = false;
  run.controller.abort();
  if (state.chatTask === run.taskId) renderChat();
}
function closeAssistant() { if (state.streaming) cancelChat(); if ($('#assistant-dialog').open) $('#assistant-dialog').close(); }
$('#assistant-close').onclick = closeAssistant;
$('#assistant-dialog').addEventListener('cancel', () => { if (state.streaming) cancelChat(); });
$('#assistant-settings').onclick = openSettings;
$('#chat-cancel').onclick = () => { cancelChat(); $('#chat-error').textContent = '已停止回答。你可以重试，或换一个问题。'; };
$('#chat-clear').onclick = () => { if (state.streaming) return; state.histories.set(state.chatTask, []); state.lastQuestion = ''; $('#chat-error').textContent = ''; renderChat(); };
$('#chat-retry').onclick = () => { const history = historyFor(state.chatTask); const index = history.findLastIndex(message => message.role === 'user'); if (index >= 0) { const question = history[index].text; history.splice(index); sendChat(question); } };
$('#chat-log').onclick = event => {
  const button = event.target.closest('[data-retry-message]');
  if (!button || state.streaming) return;
  const history = historyFor(state.chatTask), index = Number(button.dataset.retryMessage);
  const question = history.slice(0, index).findLast(message => message.role === 'user')?.text;
  if (question) { history.splice(index - 1); sendChat(question); }
};
$('#chat-suggestions').onclick = event => { const button = event.target.closest('[data-question]'); if (button) sendChat(button.dataset.question); };
$('#chat-form').onsubmit = event => { event.preventDefault(); sendChat($('#chat-question').value); };
$('#chat-question').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!state.streaming) sendChat(event.target.value); } };
async function sendChat(question) {
  question = question.trim();
  if (!question || state.streaming || !state.detail || state.chatTask !== state.detail.task_id) return;
  const detail = state.detail, taskId = state.chatTask;
  const history = historyFor(taskId);
  const previous = chatHistory(history);
  history.push({ role: 'user', text: question }, { role: 'assistant', text: '', status: 'pending' });
  const reply = history[history.length - 1];
  const controller = new AbortController();
  const run = { controller, taskId, reply };
  state.chatRun = run;
  const isCurrent = () => state.chatRun === run && !controller.signal.aborted;
  state.chatController = controller; state.streaming = true; state.lastQuestion = question;
  $('#chat-question').value = ''; $('#chat-error').textContent = ''; renderChat();
  let completed = false;
  try {
    const payload = { repo: detail.repo, number: detail.number, question, history: previous, mode: state.mode };
    if (state.mode === 'model' && state.key) payload.api_key = state.key;
    const response = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify(payload), signal: controller.signal });
    if (!response.ok) { let message = '这次回答未完成，请重试。'; try { message = (await response.json()).message || message; } catch {} throw new Error(message); }
    if (!response.body) throw new Error('当前浏览器未能读取回答，请重试。');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    function consume(frame) {
      if (!isCurrent()) throw new DOMException('请求已取消', 'AbortError');
      let eventName = 'message'; const lines = [];
      frame.split('\n').forEach(line => { if (line.startsWith('event:')) eventName = line.slice(6).trim(); else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart()); });
      if (!lines.length) return;
      const data = JSON.parse(lines.join('\n'));
      if (eventName === 'meta' && data.task_id !== taskId) throw new Error('回答关联的任务发生变化，请重新打开助手。');
      if (eventName === 'token' && typeof data.text === 'string') { reply.text += data.text; if (state.chatTask === taskId) renderChat(); }
      if (eventName === 'error') throw new Error(data.message || '这次回答未完成，请重试。');
      if (eventName === 'done') { if (data.task_id !== taskId) throw new Error('回答关联的任务发生变化，请重新打开助手。'); completed = true; }
    }
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) { const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); consume(frame); }
      if (done) { if (buffer.trim()) consume(buffer); break; }
    }
    if (!isCurrent()) return;
    if (!completed || !reply.text.trim()) throw new Error('回答连接已中断，可以点击重新回答。');
    reply.status = 'complete';
  } catch (error) {
    if (isCurrent()) {
      const message = error.name === 'AbortError' ? '回答已停止，请重新回答。' : error.message || '回答未完成，请重试。';
      failReply(reply, message);
      controller.abort();
    }
  } finally {
    if (state.chatRun === run) {
      state.streaming = false; state.chatController = null; state.chatRun = null;
      if (state.chatTask === taskId) renderChat();
    }
  }
}
$('.skip-link').onclick = event => { event.preventDefault(); main.focus({ preventScroll: true }); };
window.addEventListener('hashchange', renderRoute);
api('/api/settings').then(settings => { state.settings = settings; state.mode = settings.model_ready ? 'model' : 'local'; updateConnection(); }).catch(() => { updateConnection(); });
renderRoute();

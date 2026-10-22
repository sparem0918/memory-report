'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const companies = {samsung: '삼성전자', sk: 'SK하이닉스'};
let data = {articles: [], targets: []}, view = 'overview', company = 'all', topic = 'all', demo = false, limit = 15, targetLimit = 8;
let saved = new Set();
try { saved = new Set(JSON.parse(localStorage.getItem('memory-saved') || '[]')); } catch (_) {}
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const safeURL = (value) => { try { const u = new URL(value); return u.protocol === 'https:' ? esc(u.href) : '#'; } catch (_) { return '#'; } };
const money = (n) => new Intl.NumberFormat('ko-KR').format(n) + '원';
const dateText = (value, options = {}) => new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', ...options}).format(new Date(value));
const link = (url, text, cls = '') => `<a href="${safeURL(url)}" target="_blank" rel="noopener noreferrer" class="${cls}">${text}</a>`;
let toastTimer;
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 3500); }
function demoData() {
  const stamp = new Date().toISOString();
  const entries = [
    ['sk','국내','HBM · AI','[예시] HBM 수요 확대, 고부가 메모리 공급 전략에 주목','예시 경제신문'],
    ['samsung','해외','DRAM · 메모리','[Sample] Memory demand in focus as AI infrastructure expands','Sample International News'],
    ['samsung','국내','증권가 리포트','[예시] 증권가, 메모리 업황 회복 속도와 수익성 개선에 주목','예시 증권뉴스'],
    ['sk','해외','HBM · AI','[Sample] High-bandwidth memory remains a focus for AI investment','Sample Global News'],
    ['samsung','국내','NAND','[예시] NAND 수요와 재고 정상화, 하반기 관전 포인트','예시 산업뉴스'],
    ['sk','국내','증권가 리포트','[예시] 리포트 읽기: 실적 추정과 목표주가 변경 근거 확인하기','예시 리서치'],
  ];
  return {articles: entries.map(([company,region,topic,title,source],i) => ({id:'demo-'+i,company,region,topic,title,source,published:stamp,url:'#',report:Number(topic==='증권가 리포트')})),
    targets:[{id:'demo',company:'sk',broker:'예시 증권사 A',previous:200000,target:240000,opinion:'매수 (가상)',reason:'예시: HBM 수요 증가에 따른 이익 추정치 상향. 실제 리포트가 아닌 설명용 기록입니다.',url:'#',published: new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'})}]};
}
function current() { return demo ? demoData() : data; }
function renderStatus() {
  const stamp = new Date();
  $('#today').textContent = dateText(stamp, {year:'numeric',month:'long',day:'numeric',weekday:'long'});
  $('#edition-date').textContent = dateText(stamp, {year:'numeric'});
  $('#updated').textContent = data.last_success ? `최근 수집 ${dateText(data.last_success,{hour:'2-digit',minute:'2-digit'})}` : '아직 수집된 데이터가 없습니다';
  $('#next-run').textContent = data.next_run ? `다음 수집 ${dateText(data.next_run,{hour:'2-digit',minute:'2-digit'})} KST` : '매일 08:00 · 18:00 KST';
  const btn = $('#refresh');
  btn.disabled = !!data.collecting;
  btn.innerHTML = data.collecting ? '<span>⟳</span> 수집 중…' : '<span>⟳</span> 지금 수집하기';
  const failed = data.last_run && data.last_run.successful < 6;
  const notice = $('#notice');
  notice.classList.toggle('visible', !demo && (failed || !data.articles.length));
  if (failed) {
    notice.innerHTML = `<span><strong>${data.last_run.successful ? '일부 수집 지연' : '뉴스 수집 연결을 확인해 주세요'}</strong> · ${data.last_run.successful}/6개 피드 수집 완료. ${data.last_run.successful ? '기존 기사와 수집된 결과를 표시합니다.' : '환경 설정에서 news.google.com 접근을 허용한 뒤 다시 수집하세요.'}</span><button data-action="demo">예시 화면 살펴보기 ↗</button>`;
  } else {
    notice.innerHTML = '<span>첫 브리핑을 준비하고 있습니다. 지금 수집하거나, 예시 화면에서 구성을 먼저 살펴보세요.</span><button data-action="demo">예시 화면 살펴보기 ↗</button>';
  }
  $('#demo-banner').hidden = !demo;
}
function renderHighlights(articles) {
  const cutoff = Date.now() - 72*3600*1000;
  const recent = articles.filter(a => new Date(a.published).getTime() >= cutoff);
  const selected = [], seen = new Set(), sources = new Set();
  // Prefer a mix of topics and publishers rather than three copies of one story.
  for (const a of recent) {
    if (!seen.has(a.topic) && !sources.has(a.source)) { selected.push(a); seen.add(a.topic); sources.add(a.source); }
    if (selected.length === 3) break;
  }
  for (const a of recent) { if (selected.length < 3 && !selected.some(b => b.id === a.id)) selected.push(a); }
  const prompts = [
    ['기업별 주요 변화', '삼성전자와 SK하이닉스의 새로운 소식을 모아 기업별로 정리합니다.'],
    ['HBM부터 메모리 업황까지', 'HBM · DRAM · NAND 관련 기사를 주제별로 살펴볼 수 있습니다.'],
    ['증권가의 시선', '목표주가와 투자의견 관련 기사를 모아 원문 확인을 돕습니다.'],
  ];
  $('#highlights').innerHTML = [0,1,2].map(i => {
    const a = selected[i];
    return `<article class="highlight"><div class="highlight-top"><span class="highlight-tag">${esc(a?.topic || '수집 대기')}</span><span class="highlight-number">0${i+1}</span></div><h3>${a ? (demo ? esc(a.title) : link(a.url,esc(a.title))) : prompts[i][0]}</h3>${!a ? `<p>${prompts[i][1]}</p>` : ''}<div class="highlight-bottom"><span>${a ? esc(a.source) : '첫 수집 후 기사가 표시됩니다'}</span>${a && !demo ? link(a.url,'원문 읽기 ↗') : '<span>↗</span>'}</div></article>`;
  }).join('');
  $('#company-cards').innerHTML = Object.entries(companies).map(([key,name]) => {
    const items = recent.filter(a=>a.company === key);
    const topics = [...new Set(items.map(a=>a.topic))].slice(0,3);
    return `<article class="company-card"><div class="company-card-header"><span class="company-avatar ${key==='sk'?'sk':'samsung'}">${key==='sk'?'SK':'S'}</span><div><h3>${name}</h3><span class="stock-code">${key==='sk'?'000660':'005930'} · KOSPI</span></div><button class="company-more" data-company="${key}">소식 보기 ↗</button></div><div class="company-card-content"><div class="company-stat"><small>최근 72시간 수집 기사</small><strong>${items.length}<span>건</span></strong></div><div class="company-topics">${topics.length ? topics.map(t=>`<span class="topic-small">${esc(t)}</span>`).join('') : '<span class="company-no-data">새로운 소식이 모이면<br>주요 주제를 알려드려요.</span>'}</div></div></article>`;
  }).join('');
}
function renderAIBriefing() {
  const expanded = new Set($$('#ai-briefing details[open]').map(node=>node.dataset.record));
  const ai = demo ? null : data.gemini;
  const brief = ai?.briefing;
  const retryNote = ai?.retry_at && ai.status==='error' ? ` 다음 생성 시도 가능: ${dateText(ai.retry_at,{hour:'2-digit',minute:'2-digit'})} KST. 시간이 지난 뒤 ‘지금 수집하기’를 눌러 주세요.` : '';
  $('#highlights').hidden = !!brief;
  $('#briefing-basis').textContent = brief ? 'Gemini 한국어 요약 · 출처 함께 확인' : '최근 72시간 · 제목 기반 핵심 기사';
  if (!brief) {
    $('#ai-briefing').innerHTML = !demo && ai ? `<p class="ai-status" role="status">${esc(ai.message+retryNote)}</p>` : '';
    return;
  }
  const old = ai.status !== 'ready' || Date.now()-new Date(brief.generated_at).getTime()>24*3600*1000;
  const sourceLinks = (item,id) => !item.sources.length ? '<p class="ai-sources">확인 필요 · 검사를 통과한 기업별 요약이 없습니다.</p>' : `<details class="ai-sources" data-record="${id}" ${expanded.has(id)?'open':''}><summary>출처 ${item.sources.length}건 · ${item.basis==='title'?'제목 기반':'공개 발췌·제목 기반'}</summary>${item.sources.map(s=>link(s.url,`${esc(s.source)} · ${esc(s.title)} ↗`)).join('')}</details>`;
  const content = (item,id) => `<h4>${esc(item.title)}</h4><p class="ai-summary">${esc(item.summary)}</p><dl><dt>의미</dt><dd>${esc(item.why_it_matters)}</dd><dt>다음 확인</dt><dd>${esc(item.watch_next)}</dd></dl>${sourceLinks(item,id)}`;
  $('#ai-briefing').innerHTML = `<div class="ai-intro"><div><span class="ai-label">AI BRIEF${old?' · 이전 요약':''}${brief.model===ai.fallback_model&&brief.model!==ai.model?' · Flash Lite 대체':''}</span><h3>${esc(brief.headline)}</h3></div><span class="ai-updated">${dateText(brief.generated_at,{hour:'2-digit',minute:'2-digit'})} KST 생성</span></div>${old?`<p class="ai-status" role="status">${esc(ai.status==='ready'?'새 자료가 수집되면 요약을 갱신합니다.':ai.message+retryNote)} 기존 요약을 표시합니다.</p>`:''}${brief.warnings?.length?'<p class="ai-status">일부 요약이 출처·수치·형식 검사에 실패해 제외되거나 ‘확인 필요’로 표시됐습니다. 검사를 통과한 내용만 표시합니다.</p>':''}<div class="ai-highlights">${brief.highlights.map((item,i)=>`<article class="ai-card"><span class="ai-number">0${i+1}</span>${content(item,'highlight-'+i)}</article>`).join('')}</div><div class="ai-companies">${brief.companies.map(item=>`<article class="ai-card ai-company"><span class="ai-company-name">${esc(companies[item.company])}</span>${content(item,'company-'+item.company)}</article>`).join('')}</div><p class="footnote ai-footnote">수집된 공개 자료를 AI가 정리한 내용입니다. 제목 기반 요약은 기사 본문을 확인한 결과가 아닙니다. 수치와 해석은 연결된 원문에서 확인하세요.</p>`;
}
function renderTargets(targets) {
  const expanded = new Set($$('#targets details[open]').map(node=>node.dataset.record));
  const filter = $('#target-filter').value;
  const list = targets.filter(t => (company === 'all' || t.company === company) && (filter==='all'||t.status===filter||filter==='manual'&&t.origin!=='auto'||filter==='priced'&&t.target!=null));
  list.sort((a,b)=>Number(b.target!=null)-Number(a.target!=null)||b.published.localeCompare(a.published));
  const collection = data.target_collection;
  $('#target-status').textContent = collection ? `최근 자동 정리 ${dateText(collection.updated,{hour:'2-digit',minute:'2-digit'})} KST${collection.errors?.length?' · 일부 공개 자료 접근 지연':''}${collection.pdf_reader===false?' · PDF 도구 설치 필요':''}` : '매일 오전 8시 · 오후 6시 자동 수집 · 원문에 없는 값은 확인 필요';
  $('#target-status').title = collection?.errors?.join('\n') || '';
  $('#more-targets').hidden = list.length<=targetLimit;
  $('#targets-empty').hidden = !!list.length;
  $('#targets-empty').innerHTML = filter==='all' ? '<strong>아직 수집된 목표주가 관련 자료가 없습니다.</strong>지금 수집하기를 누르면 공개 기사·리포트를 확인해 자동 등록합니다.' : '<strong>선택한 조건에 맞는 기록이 없습니다.</strong>전체 기록에서 다른 상태의 자료를 확인할 수 있습니다.';
  const unknown = '<span class="needs-review">확인 필요</span>';
  const price = value => value==null ? unknown : money(value);
  const kinds = {rss:'공개 RSS 문구',public_article:'공개 기사 본문',public_report:'공개 리포트 정보 · PDF 1쪽',report_metadata:'공개 리포트 정보'};
  $('#targets').innerHTML = list.slice(0,targetLimit).map(t => {
    const change = t.previous>0&&t.target>0 ? (t.target/t.previous-1)*100 : null;
    const automatic = t.origin==='auto';
    const status = automatic ? (t.status==='review'?'확인 필요':'자동 추출') : '직접 기록';
    const badge = `<span class="record-status ${automatic?t.status:'manual'}" title="${esc((t.missing_fields||[]).join(', '))}">${status}</span>`;
    const changeCell = change===null ? unknown : `<span class="change ${change<0?'down':change===0?'flat':''}">${change>0?'+':''}${change.toFixed(1)}%</span>`;
    const detail = automatic ? `<details class="extraction-evidence" data-record="${esc(t.id)}" ${expanded.has(String(t.id))?'open':''}><summary>추출 근거 보기${t.missing_fields?.length?' · '+esc(t.missing_fields.join(' · ')):''}</summary><p class="evidence-source">${esc(kinds[t.source_kind]||'공개 자료')} · ${esc(t.source)} · ${esc(t.title)}</p><blockquote>${esc(t.evidence)}</blockquote>${(t.notes||[]).map(note=>`<p>${esc(note)}</p>`).join('')}</details>` : '';
    return `<tr><td class="table-company">${companies[t.company]}<small>${esc(t.broker||'증권사 확인 필요')}</small></td><td>${esc(t.published.slice(0,10))}<small class="date-kind">${t.date_kind==='article'?'기사일':automatic?'리포트 등록일':'리포트 발표일'}</small></td><td class="old-price">${price(t.previous)}</td><td class="new-price">${price(t.target)}</td><td>${changeCell}</td><td class="opinion">${t.opinion?esc(t.opinion):unknown}</td><td>${badge}</td><td>${demo ? '<span class="source-link">가상 예시</span>' : link(t.url,t.source_kind==='public_report'||t.source_kind==='report_metadata'?'리포트 ↗':'원문 ↗','source-link')}</td><td>${!demo?`<button class="delete-target" data-delete="${esc(t.id)}" aria-label="${esc(t.broker||companies[t.company])} 기록 ${automatic?'숨기기':'삭제'}">×</button>`:''}</td></tr><tr class="target-reason"><td colspan="9">변경 사유 ${automatic?'(원문 발췌) ':''}· ${t.reason?esc(t.reason):unknown}${detail}</td></tr>`;
  }).join('');
}
function filteredArticles(articles) {
  const search = $('#search').value.trim().toLowerCase();
  const region = $('#region').value;
  return articles.filter(a => (company==='all'||a.company===company) && (topic==='all'||a.topic===topic) && (region==='all'||a.region===region) && (view!=='reports'||a.report) && (view!=='saved'||saved.has(a.id)) && (!search||(a.title+' '+a.source).toLowerCase().includes(search)));
}
function renderNews(articles) {
  const list = filteredArticles(articles);
  $('#article-count').textContent = list.length;
  $('#nav-count').textContent = articles.length;
  $('#load-more').hidden = list.length<=limit;
  if (!list.length) {
    const empty = articles.length || view==='saved';
    $('#news-list').innerHTML = `<div class="empty-news"><div class="empty-symbol">▤</div><h3>${empty?'조건에 맞는 기사가 없습니다':'아직 도착하지 않은 첫 번째 브리핑'}</h3><p>${view==='saved'?'기사 오른쪽의 ☆ 버튼을 눌러 나중에 읽을 기사를 보관하세요.':empty?'검색어나 기업·주제 필터를 바꿔보세요.':'국내외 주요 소식을 모아 이곳에서 보여드립니다.<br>수집이 완료되면 출처와 발표 시각을 함께 확인할 수 있습니다.'}</p><button data-action="${empty?'reset':'demo'}">${empty?'필터 초기화':'예시 브리핑 살펴보기 ↗'}</button></div>`;
    return;
  }
  $('#news-list').innerHTML = list.slice(0,limit).map((a,i) => `<article class="news-item"><span class="article-index">${String(i+1).padStart(2,'0')}</span><div class="news-content"><div class="article-tags"><span class="company-tag ${a.company==='sk'?'sk-tag':''}">${companies[a.company]}</span><span class="tag-divider">/</span><span class="article-topic">${esc(a.topic)}</span>${a.report?'<span class="report-tag">리포트 관련 기사</span>':''}</div>${demo?`<span class="news-title">${esc(a.title)}</span>`:link(a.url,esc(a.title),'news-title')}<div class="article-meta"><span class="meta-source">${esc(a.source)}</span><span>·</span><span>${esc(a.region)}</span><span>·</span><time datetime="${esc(a.published)}">${dateText(a.published,{hour:'2-digit',minute:'2-digit'})} KST</time>${demo?'<span>· 가상 예시</span>':''}</div></div><div class="article-actions"><button class="save-article ${saved.has(a.id)?'saved':''}" data-save="${esc(a.id)}" aria-label="${saved.has(a.id)?'저장 취소':'기사 저장'}" aria-pressed="${saved.has(a.id)}">${saved.has(a.id)?'★':'☆'}</button>${!demo?link(a.url,'↗'):''}</div></article>`).join('');
}
function render() {
  const source = current();
  renderStatus(); renderHighlights(source.articles); renderAIBriefing(); renderTargets(source.targets); renderNews(source.articles);
  $('#overview-section').hidden = view !== 'overview';
  $('#targets-section').hidden = view === 'saved' || view === 'news';
  const names = {overview:'오늘의 브리핑',news:'전체 뉴스',reports:'증권가 리포트',saved:'저장한 기사'};
  $('#breadcrumb').textContent = names[view];
  $('#page-title').innerHTML = view==='overview' ? '반도체의 흐름을, 한눈에<span>.</span>' : esc(names[view])+'<span>.</span>';
  $('#news-title').textContent = view==='saved'?'나중에 읽을 기사':view==='reports'?'리포트 관련 뉴스':'뉴스 & 인사이트';
  $$('.nav-item').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);if(b.dataset.view===view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  $$('.tab').forEach(b=>{b.classList.toggle('active',b.dataset.filter===company);b.setAttribute('aria-pressed',b.dataset.filter===company);});
  $$('.chip').forEach(b=>{b.classList.toggle('active',b.dataset.topic===topic);b.setAttribute('aria-pressed',b.dataset.topic===topic);});
}
async function load() {
  try { const response = await fetch('/api/dashboard?target_format=2'); if(!response.ok) throw new Error(); data = await response.json(); render(); }
  catch (_) { $('#notice').classList.add('visible'); $('#notice').textContent = '서버에 연결하지 못했습니다. 서버 실행 상태를 확인한 뒤 새로고침하세요.'; }
}
document.addEventListener('click', async event => {
  const b = event.target.closest('button'); if(!b) return;
  if(b.dataset.view) { view=b.dataset.view; company='all'; topic='all'; limit=15; render(); }
  if(b.dataset.company) { company=b.dataset.company; view='news'; limit=15; render(); }
  if(b.dataset.filter) { company=b.dataset.filter; limit=15; render(); }
  if(b.dataset.topic) { topic=b.dataset.topic; limit=15; render(); }
  if(b.dataset.action==='demo') { demo=true; render(); }
  if(b.dataset.action==='reset') { company='all';topic='all';$('#search').value='';$('#region').value='all';render(); }
  if(b.dataset.save) {
    const id=b.dataset.save; if(saved.has(id))saved.delete(id);else saved.add(id);
    try {localStorage.setItem('memory-saved',JSON.stringify([...saved]));} catch (_) {toast('브라우저 저장 공간에 접근할 수 없습니다.');}
    render();
  }
  if(b.dataset.delete && confirm(b.dataset.delete.startsWith('auto-')?'이 자동 기록을 숨길까요? 이후 수집에서도 숨김 상태를 유지합니다.':'이 목표주가 기록을 삭제할까요?')) {
    try { const r=await fetch('/api/targets/'+b.dataset.delete,{method:'DELETE'});if(!r.ok)throw new Error();await load();toast('기록을 삭제했습니다.'); } catch (_) {toast('삭제하지 못했습니다. 다시 시도하세요.');}
  }
});
$('#exit-demo').addEventListener('click',()=>{demo=false;render();});
$('#search').addEventListener('input',()=>{limit=15;renderNews(current().articles);});
$('#region').addEventListener('change',()=>{limit=15;renderNews(current().articles);});
$('#load-more').addEventListener('click',()=>{limit+=15;renderNews(current().articles);});
$('#target-filter').addEventListener('change',()=>{targetLimit=8;renderTargets(current().targets);});
$('#more-targets').addEventListener('click',()=>{targetLimit+=8;renderTargets(current().targets);});
$('#refresh').addEventListener('click',async()=>{
  demo=false;data.collecting=true;render();
  try {const r=await fetch('/api/refresh',{method:'POST'});if(!r.ok)throw new Error();toast('뉴스와 목표주가 자료를 수집하고 있습니다.');setTimeout(load,1000);} catch (_) {data.collecting=false;render();toast('수집을 시작하지 못했습니다. 다시 시도하세요.');}
});
$('#add-target').addEventListener('click',()=>{
  if(demo){toast('실제 데이터 화면에서 리포트를 기록해 주세요.');return;}
  const date = new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
  const input=$('[name="published"]');input.value=date;input.max=date;$('#form-error').textContent='';$('#target-dialog').showModal();
});
$('#close-dialog').addEventListener('click',()=>$('#target-dialog').close());
$('#target-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('.form-submit');button.disabled=true;
  const payload=Object.fromEntries(new FormData(event.target));payload.previous=Number(payload.previous);payload.target=Number(payload.target);
  try {const r=await fetch('/api/targets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await r.json();if(!r.ok)throw new Error(result.error||'저장에 실패했습니다.');$('#target-dialog').close();event.target.reset();await load();toast('목표주가 기록을 저장했습니다.');}
  catch(e){$('#form-error').textContent=e.message;}finally{button.disabled=false;}
});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('#target-dialog').open){e.preventDefault();$('#search').focus();}});
render();load();setInterval(load,12000);

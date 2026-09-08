'use strict';
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
 sort:'M4 6h16M4 12h11M4 18h6',
 grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 compact:'M3 3h4v4H3z M10 3h4v4h-4z M17 3h4v4h-4z M3 10h4v4H3z M10 10h4v4h-4z M17 10h4v4h-4z M3 17h4v4H3z M10 17h4v4h-4z M17 17h4v4h-4z',
 heart:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',
 folder:'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
 tag:'M20 13 11 22 2 13V3h10l8 8a1.4 1.4 0 0 1 0 2ZM7 7h.01',
 plus:'M12 5v14M5 12h14',minus:'M5 12h14',close:'m6 6 12 12M6 18 18 6',
 search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
 checksquare:'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
 check:'m5 12 4 4L19 6',refresh:'M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 2l1 4M4 12l1 4a8 8 0 0 0 13 2',
 settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3ZM15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0Z',
 logout:'M9 21H4V3h5M8 12h13m-5-5 5 5-5 5',harddrive:'M5 4h14l3 12H2L5 4ZM2 16v4h20v-4M6 18h.01M10 18h.01',
 menu:'M3 6h18M3 12h18M3 18h18',images:'M4 4h15v15H4zM8 8h.01M4 15l5-5 5 5 3-3 2 2M9 22h13V9',
 more:'M5 12h.01M12 12h.01M19 12h.01',left:'m15 5-7 7 7 7',right:'m9 5 7 7-7 7',
 play:'m8 4 12 8-12 8V4Z',pause:'M8 4v16M16 4v16',expand:'M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5',
 info:'M12 11v6M12 7h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',download:'M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6',
 lock:'M6 10V7a6 6 0 0 1 12 0v3M4 10h16v12H4zM12 14v4'
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.images}"/></svg>`;
function icons(root=document) { $$('[data-icon]',root).forEach(el => { el.innerHTML=icon(el.dataset.icon); el.removeAttribute('data-icon'); }); }
icons();
const state = {items:[],total:0,summary:null,view:'all',folder:'',tags:[],q:'',sort:'newest',recursive:false,selected:new Set(),selecting:false,loading:false,generation:0,viewerIndex:-1,original:false,scale:1,x:0,y:0,slide:null,me:null};
let toastTimer, searchTimer, refreshTimer, loadingPromise;
function toast(message) { const host=$('#modal').open?$('#modal'):$('#viewer').open?$('#viewer'):document.body;host.append($('#toast'));$('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,6000); }
async function api(path, method='GET', body) {
  const response=await fetch('/api'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Luma-Request':'1'},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  let result; try { result=await response.json(); } catch { result={detail:'サーバーから応答を取得できませんでした。'}; }
  if(!response.ok) {
    if(response.status===401 && !['/login','/me'].includes(path)) { closeViewer(); $('#modal').close(); await initialize(); }
    throw new Error(typeof result.detail==='string'?result.detail:'入力内容を確認してください。パスワードは12文字以上、タグは30個までです。');
  }
  return result;
}
function safe(fn) { return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>toast(e.message)); }
const formatBytes = n => n>=1024**3?(n/1024**3).toFixed(1)+' GB':n>=1024**2?(n/1024**2).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB';
const media = (item,kind) => `/api/media/${encodeURIComponent(item.id)}/${kind}?v=${encodeURIComponent(item.version)}`;
const tagList = text => [...new Set(text.split(/[,、\n]/).map(t=>t.trim()).filter(Boolean))];
const errorBox = '<div class="form-error" role="alert"></div>';
function formBusy(form, busy) { $$('button[type="submit"]',form).forEach(b=>b.disabled=busy); }
function bindForm(form, fn) { form.addEventListener('submit',async event=>{event.preventDefault(); const error=$('.form-error',form); if(error) error.textContent=''; formBusy(form,true); try { await fn(new FormData(form)); } catch(e) {if(error) error.textContent=e.message; else toast(e.message);} finally {formBusy(form,false);} }); }

async function initialize() {
  clearInterval(refreshTimer); stopSlideshow();
  const status=await api('/status');
  let me=null; if(status.configured) { try {me=await api('/me');} catch{} }
  $('#auth').hidden=!!me; $('#app').hidden=!me;
  if(me) {state.me=me; $('#avatar').textContent=me.username.slice(0,1).toUpperCase(); await refreshSummary(); await loadImages(true); refreshTimer=setInterval(safe(poll),5000); return;}
  state.items=[];state.selected.clear();$('#gallery').replaceChildren();$('#viewer-image').removeAttribute('src');$('#filmstrip').replaceChildren();$('#image-info').replaceChildren();
  if(!status.configured && !status.local) { $('#auth-form').innerHTML='<div class="eyebrow">WELCOME TO LUMA</div><h1>準備がもう少し。</h1><p class="auth-intro">このPCの <strong>localhost:8790</strong> を開き、管理者IDとパスワードを設定してください。</p>';return; }
  const setup=!status.configured;
  $('#auth-form').innerHTML=`<div class="eyebrow">${setup?'MAKE YOURSELF AT HOME':'WELCOME BACK'}</div><h1>${setup?'あなたのライブラリを、はじめよう。':'おかえりなさい。'}</h1><p class="auth-intro">${setup?'画像を置くだけで、いつものスマホがビュアーに。<br>まずは管理者のログイン情報を設定します。':'あなたのコレクションが、待っています。<br>IDとパスワードでログインしてください。'}</p><form id="login-form">${errorBox}<div class="field"><label for="username">${setup?'管理者ID':'ID'}</label><input id="username" name="username" autocomplete="username" required maxlength="64" placeholder="IDを入力"></div><div class="field"><label for="password">パスワード${setup?'（12文字以上）':''}</label><input id="password" name="password" type="password" autocomplete="${setup?'new-password':'current-password'}" required minlength="${setup?12:1}" maxlength="256" placeholder="${setup?'12文字以上で設定':'パスワードを入力'}"></div>${setup?`<div class="field"><label for="password-confirm">パスワード（確認）</label><input id="password-confirm" name="confirm" type="password" autocomplete="new-password" required minlength="12" maxlength="256"></div><div class="field"><label>公開用フォルダ</label><code class="path-box">${esc(status.public_dir)}</code><small>ここに画像やフォルダを入れると自動で読み込みます。<br>IPフィルタは初期状態では無効です。ログイン後に設定できます。</small></div>`:''}<button type="submit" class="button primary full-width">${setup?'ライブラリを作成':'ログイン'} ${icon('right')}</button></form>`;
  bindForm($('#login-form'),async data=>{
    if(setup && data.get('password')!==data.get('confirm')) throw new Error('確認用パスワードが一致しません。');
    const body={username:data.get('username'),password:data.get('password')};
    await api(setup?'/setup':'/login','POST',body);
    $('#login-form').reset();
    if(setup) {toast('設定が完了しました。設定したIDでログインしてください。');}
    await initialize();
  });
}

async function refreshSummary() {
  const summary=await api('/summary');state.summary=summary;
  $('#all-count').textContent=summary.count.toLocaleString();$('#fav-count').textContent=summary.favorites.toLocaleString();
  $('#storage-count').textContent=summary.count.toLocaleString()+' images';$('#storage-size').textContent=formatBytes(summary.bytes)+' · このPCに保存';
  renderFolderTree();
  $('#tag-nav').innerHTML=summary.tags.length?summary.tags.map(t=>`<button class="tag-chip ${state.tags.includes(t.tag)?'active':''}" data-tag="${esc(t.tag)}" title="${esc(t.tag)} · ${t.count}枚">${esc(t.tag)}</button>`).join(''):'<span class="hint">画像にタグを付けて整理</span>';
  $('#scan-status').textContent=summary.scan.running?'スキャン中…':summary.scan.message||'';
  return summary;
}
let lastScan=null;
async function poll() {
  if(document.hidden||$('#modal').open)return;
  const before=state.summary?.count;
  const summary=await refreshSummary();
  const changed=lastScan!==null && summary.scan.revision!==lastScan;
  lastScan=summary.scan.revision;
  if((changed||before!==summary.count)&&!$('#viewer').open&&!state.selecting&&window.scrollY<300) await loadImages(true);
}
function updateHeading() {
  const name=state.folder?state.folder.split('/').pop():state.view==='favorites'?'お気に入り':'ライブラリ';
  $('#view-title').innerHTML=esc(name)+'<span class="heading-dot">.</span>';$('#breadcrumb').textContent=name;
  $('#view-description').hidden=true;
  $$('#main-nav .nav-item').forEach(b=>b.classList.toggle('active',(!state.folder)&&b.dataset.view===state.view));
  $('#parent-folder').hidden=!state.folder;
  $('#active-filters').innerHTML=state.tags.map(t=>`<button class="tag-chip active" data-remove-tag="${esc(t)}">${esc(t)} ×</button>`).join('');
}
async function changeView(view,folder='') {state.view=view;state.folder=folder;state.selected.clear();state.selecting=false;closeSidebar();updateSelection();updateHeading();await refreshSummary();await loadImages(true);window.scrollTo({top:0});}
function queryParams(offset) {const p=new URLSearchParams({q:state.q,sort:state.sort,offset,limit:80,recursive:false,favorite:state.view==='favorites',folder:state.folder||''});state.tags.forEach(t=>p.append('tag',t));return p;}
async function loadImages(reset=false) {
  if(state.loading&&!reset)return loadingPromise;
  const generation=reset?++state.generation:state.generation;
  if(reset){state.items=[];$('#gallery').replaceChildren(...childFolders().map(makeFolderCard));}
  state.loading=true;$('#loading').hidden=false;$('#load-more').hidden=true;
  const offset=state.items.length;
  loadingPromise=(async()=>{try{
    const response=await api('/images?'+queryParams(offset));
    if(generation!==state.generation)return;
    state.total=response.total;state.items.push(...response.items);
    const fragment=document.createDocumentFragment();response.items.forEach(item=>fragment.append(makeCard(item)));$('#gallery').append(fragment);
    $('#result-count').textContent=response.total.toLocaleString();$('#empty-state').hidden=response.total!==0||childFolders().length>0;
    const filtered=!!(state.q||state.tags.length||state.view==='favorites'||state.folder!==null);
    $('#empty-title').textContent='画像がありません';
    $('#empty-description').innerHTML=filtered?'検索条件を変えるか、このフォルダに画像を追加してください。':'<code>public</code> フォルダに画像を入れると、自動でここに並びます。<br>フォルダごとの追加にも対応しています。';
    $('#load-more').hidden=state.items.length>=state.total;
  }finally{if(generation===state.generation){state.loading=false;$('#loading').hidden=true;}}})();
  return loadingPromise;
}
function makeCard(item) {
  const card=document.createElement('article');card.className='image-card'+(state.selected.has(item.id)?' selected':'');card.dataset.id=item.id;
  card.draggable=true;
  card.innerHTML=`<div class="card-visual"><button class="card-open" aria-label="${esc(item.name)}を開く"><img src="${media(item,'thumb')}" loading="lazy" decoding="async" width="480" height="480" alt="${esc(item.name)}"></button><button class="card-select ${state.selected.has(item.id)?'selected':''}" aria-label="${esc(item.name)}を選択" aria-pressed="${state.selected.has(item.id)}">${icon('check')}</button><button class="card-favorite ${item.favorite?'is-favorite':''}" aria-label="お気に入りを切り替え" aria-pressed="${!!item.favorite}">${icon('heart')}</button>${item.animated?'<span class="image-badge">ANIMATED</span>':item.format==='TIFF'?'<span class="image-badge">TIFF</span>':''}</div><div class="card-meta"><span class="card-name" title="${esc(item.name)}">${esc(item.name)}</span><button class="card-menu icon-button" aria-label="${esc(item.name)}の情報・編集">${icon('more')}</button></div><div class="card-subtitle"><span class="card-folder">${esc(item.folder||'ライブラリ')}</span><span>·</span><span>${item.width} × ${item.height}</span>${item.tags.length?`<span>· ${esc(item.tags[0])}</span>`:''}</div>`;
  $('img',card).addEventListener('error',e=>{e.target.removeAttribute('src');e.target.alt='プレビューを読み込めません';});
  return card;
}
function replaceCard(item) {const old=$(`.image-card[data-id="${item.id}"]`);if(old)old.replaceWith(makeCard(item));}
function updateItem(item) {const index=state.items.findIndex(i=>i.id===item.id);if(index>=0)state.items[index]=item;replaceCard(item);}
$('#gallery').addEventListener('click',safe(async e=>{
  const card=e.target.closest('.image-card');if(!card)return;
  const index=state.items.findIndex(i=>i.id===card.dataset.id), item=state.items[index];
  if(e.target.closest('.card-favorite')){const updated=await api('/images/'+item.id,'PATCH',{favorite:!item.favorite});updateItem(updated);await refreshSummary();return;}
  if(e.target.closest('.card-select')||state.selecting){state.selecting=true;toggleSelection(item.id);return;}
  if(e.target.closest('.card-open')||e.target.closest('.card-menu')){await openViewer(index);if(e.target.closest('.card-menu'))toggleInfo(true);}
}));
function toggleSelection(id) {if(state.selected.has(id))state.selected.delete(id);else{if(state.selected.size>=200){toast('一度に選択できるのは200枚までです。');return;}state.selected.add(id);}const item=state.items.find(i=>i.id===id);if(item)replaceCard(item);updateSelection();}
function updateSelection() {$('#gallery').classList.toggle('selecting',state.selecting);$('#selection-bar').hidden=!state.selecting;$('#selected-count').textContent=state.selected.size;$('#select-button').classList.toggle('primary',state.selecting);$('#select-button').setAttribute('aria-pressed',state.selecting);['batch-tags','batch-move','batch-favorite'].forEach(id=>$('#'+id).disabled=!state.selected.size);}
$('#select-button').onclick=()=>{state.selecting=!state.selecting;if(!state.selecting){state.selected.clear();$$('.image-card').forEach(c=>c.classList.remove('selected'));$$('.card-select').forEach(b=>{b.classList.remove('selected');b.setAttribute('aria-pressed','false');});}updateSelection();};
$('#clear-selection').onclick=()=>{state.selecting=true;$('#select-button').click();};
$('#select-loaded').onclick=()=>{state.items.slice(0,200).forEach(i=>state.selected.add(i.id));$('#gallery').replaceChildren(...childFolders().map(makeFolderCard),...state.items.map(makeCard));updateSelection();};
function folderOptions(selected='') {return (state.summary?.folders||[]).map(f=>`<option value="${esc(f.path)}" ${f.path===selected?'selected':''}>${esc(f.path||'ライブラリ直下')}</option>`).join('');}
function modal(title,html) {$('#modal-title').textContent=title;$('#modal-body').innerHTML=html;icons($('#modal'));if(!$('#modal').open)$('#modal').showModal();}
$('#close-modal').onclick=()=>$('#modal').close();
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
async function batchAction(body) {
  const result=await api('/batch','POST',{ids:[...state.selected],...body});
  const failures=result.results.filter(r=>!r.ok);state.selected=new Set(failures.map(r=>r.id));
  if(!failures.length){$('#modal').close();state.selecting=false;toast(`${result.results.length}枚の画像を更新しました。`);}else{const message=`${result.results.length-failures.length}枚を更新、${failures.length}枚は変更できませんでした。\n${failures[0].error}`;const box=$('#modal .form-error');if(box)box.textContent=message;toast(message);}
  updateSelection();await refreshSummary();await loadImages(true);
}
$('#batch-tags').onclick=()=>{modal('選択した画像のタグを編集',`<form id="batch-form">${errorBox}<p class="hint">${state.selected.size}枚の画像に適用します。既存のタグは保持されます。</p><div class="field"><label for="add-tags">追加するタグ</label><input id="add-tags" name="add" placeholder="イラスト, お気に入り, 旅行"><small>カンマで区切って複数指定できます。</small></div><div class="field"><label for="remove-tags">外すタグ</label><input id="remove-tags" name="remove"></div><button type="submit" class="button primary full-width">タグを更新</button></form>`);bindForm($('#batch-form'),d=>batchAction({add_tags:tagList(d.get('add')),remove_tags:tagList(d.get('remove'))}));};
$('#batch-move').onclick=()=>{modal('選択した画像を移動',`<form id="batch-form">${errorBox}<p class="hint">${state.selected.size}枚の実ファイルを移動します。同名ファイルは上書きしません。</p><div class="field"><label for="move-folder">移動先フォルダ</label><select id="move-folder" name="folder">${folderOptions(state.folder||'')}</select></div><button type="submit" class="button primary full-width">このフォルダへ移動</button></form>`);bindForm($('#batch-form'),d=>batchAction({folder:d.get('folder')}));};
$('#batch-favorite').onclick=safe(()=>batchAction({favorite:true}));
$('#new-folder').onclick=()=>{modal('新しいフォルダ',`<form id="folder-form">${errorBox}<div class="field"><label for="folder-parent">作成場所</label><select name="parent" id="folder-parent">${folderOptions(state.folder||'')}</select></div><div class="field"><label for="folder-name">フォルダ名</label><input id="folder-name" name="name" required maxlength="180" placeholder="例：2026年の旅行"></div><button class="button primary full-width" type="submit">フォルダを作成</button></form>`);bindForm($('#folder-form'),async d=>{await api('/folders','POST',{parent:d.get('parent'),name:d.get('name')});$('#modal').close();await refreshSummary();toast('フォルダを作成しました。');});};
$('#main-nav').onclick=safe(e=>{const b=e.target.closest('[data-view]');if(b)return changeView(b.dataset.view);});
$('#folder-nav').onclick=safe(e=>{const toggle=e.target.closest('[data-collapse]');if(toggle){const path=toggle.dataset.collapse;collapsed.has(path)?collapsed.delete(path):collapsed.add(path);renderFolderTree();return;}const b=e.target.closest('[data-folder]');if(b)return changeView(state.view,b.dataset.folder);});
async function toggleTag(tag) {state.tags=state.tags.includes(tag)?state.tags.filter(t=>t!==tag):[...state.tags,tag];updateHeading();await refreshSummary();await loadImages(true);closeSidebar();}
$('#tag-nav').onclick=safe(e=>{const b=e.target.closest('[data-tag]');if(b)return toggleTag(b.dataset.tag);});
$('#active-filters').onclick=safe(e=>{const b=e.target.closest('[data-remove-tag]');if(b)return toggleTag(b.dataset.removeTag);});
$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(safe(async()=>{state.q=$('#search').value;await loadImages(true);}),250);};

function density(compact) {$('#gallery').classList.toggle('compact',compact);$('#density-compact').classList.toggle('active',compact);$('#density-comfort').classList.toggle('active',!compact);$('#density-compact').setAttribute('aria-pressed',compact);$('#density-comfort').setAttribute('aria-pressed',!compact);try{localStorage.setItem('luma-density',compact?'compact':'comfort');}catch{}}
$('#density-compact').onclick=()=>density(true);$('#density-comfort').onclick=()=>density(false);
try{density(localStorage.getItem('luma-density')==='compact');}catch{}
$('#load-more').onclick=safe(()=>loadImages());
new IntersectionObserver(entries=>{if(entries[0].isIntersecting&&!state.loading&&state.items.length<state.total&&state.items.length<400)safe(()=>loadImages())();},{rootMargin:'400px'}).observe($('#load-trigger'));
async function requestScan(){await api('/scan','POST',{});toast('公開用フォルダをスキャンしています。');await refreshSummary();}
$('#scan-button').onclick=safe(requestScan);$('#empty-scan').onclick=safe(requestScan);
function closeSidebar(){$('#sidebar').classList.remove('open');$('#sidebar-scrim').hidden=true;}
$('#menu-button').onclick=()=>{$('#sidebar').classList.add('open');$('#sidebar-scrim').hidden=false;};$('#sidebar-scrim').onclick=closeSidebar;
$('#logout').onclick=safe(async()=>{await api('/logout','POST',{});closeSidebar();state.selected.clear();state.selecting=false;await initialize();});

$('#settings-button').onclick=safe(async()=>{
  closeSidebar();const settings=await api('/settings');
  modal('設定・アクセス管理',`<div class="field"><label>画像を入れるフォルダ</label><code class="path-box">${esc(settings.public_dir)}</code><small>サブフォルダを含め15秒ごとに確認します。コピー中の画像は次回に取り込みます。</small></div><div class="settings-status"><span>現在の端末</span><strong>${esc(state.me.client_ip)}</strong></div><section class="settings-section"><h3>IPアドレスのホワイトリスト</h3><form id="access-form">${errorBox}<label class="check-label"><input name="enabled" type="checkbox" ${settings.filter_enabled?'checked':''}>許可したIPアドレスだけアクセス可能にする</label><div class="field"><label for="whitelist">許可アドレス（1行に1つ）</label><textarea id="whitelist" name="whitelist" placeholder="192.168.0.25&#10;192.168.0.0/24">${esc(settings.whitelist.join('\n'))}</textarea><small>端末単位のIP、またはCIDR形式に対応。localhostは常に許可します。無効時もID・パスワード認証は必要です。</small></div><button class="button primary full-width" type="submit">アクセス設定を保存</button></form></section><section class="settings-section"><h3>パスワードを変更</h3><form id="password-form">${errorBox}<div class="field"><label for="current-password">現在のパスワード</label><input id="current-password" name="current" type="password" autocomplete="current-password" required maxlength="256"></div><div class="field"><label for="new-password">新しいパスワード（12文字以上）</label><input id="new-password" name="new" type="password" autocomplete="new-password" required minlength="12" maxlength="256"></div><div class="field"><label for="new-confirm">新しいパスワード（確認）</label><input id="new-confirm" name="confirm" type="password" autocomplete="new-password" required minlength="12" maxlength="256"></div><p class="hint">変更すると、すべての端末からログアウトします。</p><button class="button secondary full-width" type="submit">パスワードを変更</button></form></section><section class="settings-section"><p class="hint">${location.protocol==='https:'?'HTTPSで接続しています。':'現在はHTTP接続です。通信の暗号化が必要なLANでは、付属のHTTPS起動手順をご利用ください。'}<br>画像やタグはこのPC内に保存されます。</p></section>`);
  bindForm($('#access-form'),async d=>{await api('/settings/access','PUT',{filter_enabled:d.get('enabled')==='on',whitelist:d.get('whitelist').split(/\n/).map(v=>v.trim()).filter(Boolean)});toast('アクセス設定を保存しました。');});
  bindForm($('#password-form'),async d=>{if(d.get('new')!==d.get('confirm'))throw new Error('確認用パスワードが一致しません。');await api('/settings/password','PUT',{current_password:d.get('current'),password:d.get('new')});$('#modal').close();$('#modal-body').replaceChildren();toast('パスワードを変更しました。再ログインしてください。');await initialize();});
});

const viewer=$('#viewer'),stage=$('#stage'),viewerImage=$('#viewer-image');
let imageSequence=0;
async function openViewer(index) {state.viewerIndex=index;state.original=false;if(!viewer.open)viewer.showModal();toggleInfo(false);await showImage();}
function stopSlideshow(){clearInterval(state.slide);state.slide=null;$('#slideshow').classList.remove('active');$('#slideshow').innerHTML=icon('play');$('#slideshow').setAttribute('aria-label','スライドショーを開始');}
function closeViewer(){stopSlideshow();imageSequence++;if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});viewer.close();state.viewerIndex=-1;viewerImage.removeAttribute('src');$('#image-info').hidden=true;}
$('#close-viewer').onclick=closeViewer;viewer.addEventListener('cancel',e=>{e.preventDefault();closeViewer();});
async function showImage() {
  const item=state.items[state.viewerIndex];if(!item)return;
  const seq=++imageSequence;state.scale=1;state.x=0;state.y=0;applyTransform();
  $('#viewer-name').textContent=item.name;$('#viewer-position').textContent=`${state.viewerIndex+1} / ${state.total.toLocaleString()}`;$('#viewer-resolution').textContent=`${item.width} × ${item.height} · ${formatBytes(item.size)}`;
  $('#viewer-heart').classList.toggle('active',!!item.favorite);$('#viewer-heart').setAttribute('aria-pressed',!!item.favorite);
  $('#previous').disabled=state.viewerIndex===0;$('#next').disabled=state.viewerIndex>=state.total-1;
  const nativeSupported=['JPEG','PNG','WEBP','GIF','AVIF','BMP'].includes(item.format);
  const original=state.original||item.animated;
  $('#original-toggle').classList.toggle('active',state.original);$('#original-toggle').textContent=state.original?'軽量版':'原寸';$('#original-toggle').disabled=!nativeSupported;
  if(!nativeSupported)state.original=false;
  viewerImage.alt=item.name;$('#viewer-loading').textContent='読み込み中…';$('#viewer-loading').hidden=false;
  viewerImage.onload=()=>{if(seq===imageSequence)$('#viewer-loading').hidden=true;};
  viewerImage.onerror=()=>{if(seq===imageSequence){$('#viewer-loading').hidden=false;$('#viewer-loading').textContent='表示できません。情報パネルから原本を保存できます。';}};
  viewerImage.src=media(item,original&&nativeSupported?'original':'preview');
  renderFilmstrip();if(!$('#image-info').hidden)renderInfo();
  // Warm just the adjacent previews; never prefetch originals or the entire collection.
  for(const next of [state.items[state.viewerIndex-1],state.items[state.viewerIndex+1]])if(next){const prefetch=new Image();prefetch.src=media(next,'preview');}
}
async function navigate(delta) {
  const target=state.viewerIndex+delta;if(target<0||target>=state.total)return;
  if(target>=state.items.length)await loadImages();
  if(target<state.items.length){state.viewerIndex=target;state.original=false;await showImage();}
}
$('#previous').onclick=safe(()=>navigate(-1));$('#next').onclick=safe(()=>navigate(1));
function renderFilmstrip() {
  const start=Math.max(0,state.viewerIndex-4),end=Math.min(state.items.length,state.viewerIndex+5);
  $('#filmstrip').innerHTML=state.items.slice(start,end).map((i,j)=>`<button data-index="${start+j}" class="${start+j===state.viewerIndex?'active':''}" aria-label="${esc(i.name)}"><img src="${media(i,'thumb')}" alt="" loading="lazy"></button>`).join('');
  $('#filmstrip .active')?.scrollIntoView({block:'nearest',inline:'center'});
}
$('#filmstrip').onclick=safe(async e=>{const b=e.target.closest('[data-index]');if(b){state.viewerIndex=Number(b.dataset.index);state.original=false;await showImage();}});
function toggleInfo(force) {const show=force??$('#image-info').hidden;$('#image-info').hidden=!show;$('#info-toggle').classList.toggle('active',show);$('#info-toggle').setAttribute('aria-expanded',show);if(show){stopSlideshow();renderInfo();}state.scale=1;state.x=0;state.y=0;applyTransform();}
$('#info-toggle').onclick=()=>toggleInfo();
function renderInfo() {
  const item=state.items[state.viewerIndex];if(!item)return;
  $('#image-info').innerHTML=`<h3>画像の情報・編集</h3><form id="image-form">${errorBox}<div class="field"><label for="edit-name">ファイル名</label><input id="edit-name" name="name" value="${esc(item.name)}" required maxlength="180"></div><div class="field"><label for="edit-folder">フォルダ</label><select id="edit-folder" name="folder">${folderOptions(item.folder)}</select></div><div class="field"><label for="edit-tags">タグ</label><input id="edit-tags" name="tags" value="${esc(item.tags.join(', '))}" placeholder="イラスト, 旅行"><small class="hint">カンマ区切り・1枚につき30個まで</small></div><button class="button primary full-width" type="submit">変更を保存</button><p class="hint">名前・フォルダの変更は実ファイルに反映されます。</p></form><dl><dt>解像度</dt><dd>${item.width} × ${item.height}</dd><dt>形式</dt><dd>${esc(item.format)}${item.animated?' · アニメーション':''}</dd><dt>サイズ</dt><dd>${formatBytes(item.size)}</dd><dt>更新日</dt><dd>${esc(new Date(item.mtime_ns/1e6).toLocaleString('ja-JP'))}</dd><dt>保存場所</dt><dd>${esc(item.path)}</dd></dl><a class="button secondary full-width" href="${media(item,'download')}" download>${icon('download')}原本をダウンロード</a>`;
  bindForm($('#image-form'),async d=>{const updated=await api('/images/'+item.id,'PATCH',{name:d.get('name'),folder:d.get('folder'),tags:tagList(d.get('tags'))});updateItem(updated);await refreshSummary();$('#viewer-name').textContent=updated.name;renderInfo();toast('画像の情報を保存しました。');});
}
$('#viewer-heart').onclick=safe(async()=>{const item=state.items[state.viewerIndex];if(!item)return;const updated=await api('/images/'+item.id,'PATCH',{favorite:!item.favorite});updateItem(updated);$('#viewer-heart').classList.toggle('active',!!updated.favorite);$('#viewer-heart').setAttribute('aria-pressed',!!updated.favorite);await refreshSummary();});
$('#slideshow').onclick=()=>{if(state.slide){stopSlideshow();return;}toggleInfo(false);$('#slideshow').classList.add('active');$('#slideshow').innerHTML=icon('pause');$('#slideshow').setAttribute('aria-label','スライドショーを停止');state.slide=setInterval(safe(async()=>{if(document.hidden)return;if(state.viewerIndex>=state.total-1){stopSlideshow();return;}await navigate(1);}),5000);};
$('#fullscreen').onclick=safe(async()=>{if(document.fullscreenElement)await document.exitFullscreen();else if(viewer.requestFullscreen)await viewer.requestFullscreen();});
$('#original-toggle').onclick=safe(async()=>{state.original=!state.original;await showImage();});
function applyTransform() {
  if(state.scale<=1){state.x=0;state.y=0;}
  const maxX=Math.max(0,(viewerImage.clientWidth*state.scale-stage.clientWidth)/2),maxY=Math.max(0,(viewerImage.clientHeight*state.scale-stage.clientHeight)/2);
  state.x=Math.max(-maxX,Math.min(maxX,state.x));state.y=Math.max(-maxY,Math.min(maxY,state.y));
  viewerImage.style.transform=`translate(${state.x}px,${state.y}px) scale(${state.scale})`;
  $('#zoom-reset').textContent=state.scale===1?'フィット':Math.round(state.scale*100)+'%';
}
function zoom(next,cx=stage.clientWidth/2,cy=stage.clientHeight/2) {const old=state.scale;state.scale=Math.max(1,Math.min(10,next));const ratio=state.scale/old;state.x=cx-stage.clientWidth/2-(cx-stage.clientWidth/2-state.x)*ratio;state.y=cy-stage.clientHeight/2-(cy-stage.clientHeight/2-state.y)*ratio;applyTransform();}
$('#zoom-in').onclick=()=>zoom(state.scale*1.5);$('#zoom-out').onclick=()=>zoom(state.scale/1.5);$('#zoom-reset').onclick=()=>{state.scale=1;applyTransform();};
stage.addEventListener('wheel',e=>{e.preventDefault();const r=stage.getBoundingClientRect();zoom(state.scale*Math.exp(-e.deltaY*.002),e.clientX-r.left,e.clientY-r.top);},{passive:false});
stage.addEventListener('dblclick',e=>{if(e.target.closest('button'))return;const r=stage.getBoundingClientRect();zoom(state.scale>1?1:2.5,e.clientX-r.left,e.clientY-r.top);});
const pointers=new Map();let gesture=null,lastTap=0;
stage.addEventListener('pointerdown',e=>{
  if(e.target.closest('button'))return;stage.setPointerCapture(e.pointerId);pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(pointers.size===1)gesture={x:e.clientX,y:e.clientY,baseX:state.x,baseY:state.y,start:performance.now(),moved:false,pinched:false};
  if(pointers.size===2){const [a,b]=[...pointers.values()];gesture={...gesture,distance:Math.hypot(a.x-b.x,a.y-b.y),scale:state.scale,pinched:true,midX:(a.x+b.x)/2,midY:(a.y+b.y)/2,baseX:state.x,baseY:state.y};}
});
stage.addEventListener('pointermove',e=>{
  if(!pointers.has(e.pointerId)||!gesture)return;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(pointers.size===2){const [a,b]=[...pointers.values()];const scale=Math.max(1,Math.min(10,gesture.scale*Math.hypot(a.x-b.x,a.y-b.y)/Math.max(1,gesture.distance)));const ratio=scale/gesture.scale,r=stage.getBoundingClientRect(),centerX=r.left+r.width/2,centerY=r.top+r.height/2;state.x=(a.x+b.x)/2-centerX-(gesture.midX-centerX-gesture.baseX)*ratio;state.y=(a.y+b.y)/2-centerY-(gesture.midY-centerY-gesture.baseY)*ratio;state.scale=scale;applyTransform();return;}
  const dx=e.clientX-gesture.x,dy=e.clientY-gesture.y;if(Math.hypot(dx,dy)>8)gesture.moved=true;
  if(state.scale>1){state.x=gesture.baseX+dx;state.y=gesture.baseY+dy;applyTransform();}
});
function endPointer(e) {
  if(!pointers.has(e.pointerId)||!gesture)return;pointers.delete(e.pointerId);
  if(!pointers.size){if(!gesture.pinched&&e.type!=='pointercancel'){
    const dx=e.clientX-gesture.x,dy=e.clientY-gesture.y;
    if(state.scale===1&&Math.abs(dx)>65&&Math.abs(dx)>Math.abs(dy)*1.3&&performance.now()-gesture.start<1000)safe(()=>navigate(dx<0?1:-1))();
    else if(!gesture.moved&&e.pointerType==='touch'){const now=performance.now();if(now-lastTap<300){const r=stage.getBoundingClientRect();zoom(state.scale>1?1:2.5,e.clientX-r.left,e.clientY-r.top);lastTap=0;}else lastTap=now;}
  }gesture=null;}else{const a=[...pointers.values()][0];gesture={x:a.x,y:a.y,baseX:state.x,baseY:state.y,start:performance.now(),moved:true,pinched:true};}
}
stage.addEventListener('pointerup',endPointer);stage.addEventListener('pointercancel',endPointer);
window.addEventListener('resize',()=>{if(viewer.open)applyTransform();});
document.addEventListener('keydown',safe(async e=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName))return;
  if(viewer.open){if(e.key==='ArrowRight'){e.preventDefault();await navigate(1);}if(e.key==='ArrowLeft'){e.preventDefault();await navigate(-1);}if(e.key==='+'||e.key==='=')zoom(state.scale*1.5);if(e.key==='-')zoom(state.scale/1.5);if(e.key==='0'){state.scale=1;applyTransform();}if(e.key.toLowerCase()==='i')toggleInfo();if(e.key===' '){e.preventDefault();$('#slideshow').click();}return;}
  if($('#modal').open)return;if(e.key==='/'&&!$('#app').hidden){e.preventDefault();$('#search').focus();}if(e.key==='Escape')closeSidebar();
}));
initialize().catch(e=>{ $('#auth').hidden=false;$('#auth-form').innerHTML=`<h1>接続できませんでした。</h1><p class="auth-intro">${esc(e.message)}</p><a class="button primary" href="/">もう一度読み込む</a>`; });

// Folder browsing and authenticated transfers. No image data is sent outside this origin.
const collapsed = new Set();
function closeSort(focus=false){$('#sort-options').hidden=true;$('#sort-button').setAttribute('aria-expanded','false');if(focus)$('#sort-button').focus();}
function openSort(){const list=$('#sort-options');list.hidden=false;$('#sort-button').setAttribute('aria-expanded','true');$('[aria-selected="true"]',list)?.focus();}
$('#sort-button').onclick=()=>$('#sort-options').hidden?openSort():closeSort(true);
$('#sort-button').onkeydown=e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();openSort();}};
$('#sort-options').onclick=safe(async e=>{const option=e.target.closest('[data-sort]');if(!option)return;state.sort=option.dataset.sort;$('#sort-label').textContent=option.firstChild.textContent;$$('[data-sort]').forEach(o=>o.setAttribute('aria-selected',o===option));closeSort(true);await loadImages(true);});
$('#sort-options').onkeydown=e=>{const options=$$('[data-sort]'),index=options.indexOf(document.activeElement);if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeSort(true);}else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();let target=e.key==='Home'?0:e.key==='End'?options.length-1:(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;options[target].focus();}else if(e.key==='Tab'){closeSort();}};
document.addEventListener('click',e=>{if(!e.target.closest('.sort-menu'))closeSort();});
let dragPayload=null, uploading=false, foldersHidden=false;
function parentPath(path){return path.includes('/')?path.slice(0,path.lastIndexOf('/')):'';}
function childFolders(){return (state.summary?.folders||[]).filter(f=>f.path && parentPath(f.path)===(state.folder||''));}
function renderFolderTree(){
  const folders=state.summary?.folders||[];
  $('#folder-nav').innerHTML=folders.filter(f=>!folders.some(p=>p.path!==f.path&&collapsed.has(p.path)&&(!p.path||f.path.startsWith(p.path+'/')))).map(f=>{
    const hasChildren=folders.some(c=>c.path&&c.path!==f.path&&parentPath(c.path)===f.path);
    return `<div class="folder-tree-row" style="padding-left:${Math.min(f.path?f.path.split('/').length:0,6)*9}px">${hasChildren?`<button class="folder-toggle" data-collapse="${esc(f.path)}" aria-label="${esc(f.name)}を折り畳み・展開" aria-expanded="${!collapsed.has(f.path)}">${collapsed.has(f.path)?'▸':'▾'}</button>`:'<span class="folder-toggle"></span>'}<button class="nav-item ${state.folder===f.path?'active':''}" data-folder="${esc(f.path)}" data-drop-folder="${esc(f.path)}" ${f.path?`draggable="true" data-drag-folder="${esc(f.path)}"`:''}>${icon('folder')}<span class="folder-name">${esc(f.name)}</span><span class="nav-count">${f.count}</span></button></div>`;
  }).join('');
}
function makeFolderCard(folder){
  const card=document.createElement('article');card.className='folder-card';card.dataset.dropFolder=folder.path;card.dataset.dragFolder=folder.path;card.draggable=true;
  card.innerHTML=`<button class="folder-card-open card-visual" data-open-folder="${esc(folder.path)}" aria-label="${esc(folder.name)}を開く">${icon('folder')}<span>フォルダを開く</span></button><div class="card-meta"><strong class="card-name">${esc(folder.name)}</strong><button class="folder-move-menu icon-button" aria-label="${esc(folder.name)}を移動">${icon('more')}</button></div><div class="card-subtitle">${folder.count} 枚（配下を含む）</div>`;
  $('.folder-move-menu',card).onclick=()=>{
    modal('フォルダを移動',`<form id="folder-move-form">${errorBox}<p class="hint">${esc(folder.name)} を移動します。配下の画像とタグも保持します。</p><div class="field"><label for="folder-destination">移動先</label><select id="folder-destination" name="parent">${folderOptions(parentPath(folder.path))}</select></div><button type="submit" class="button primary full-width">移動</button></form>`);
    bindForm($('#folder-move-form'),async d=>{await api('/folders/move','POST',{source:folder.path,parent:d.get('parent')});$('#modal').close();await refreshSummary();await loadImages(true);toast('フォルダを移動しました。');});
  };
  return card;
}
$('#gallery').addEventListener('click',safe(e=>{const folder=e.target.closest('[data-open-folder]');if(folder)return changeView(state.view,folder.dataset.openFolder);}));
$('#parent-folder').onclick=safe(()=>changeView(state.view,parentPath(state.folder||'')));
$('#toggle-folders').onclick=()=>{foldersHidden=!foldersHidden;$('#folder-nav').hidden=foldersHidden;$('#toggle-folders').setAttribute('aria-expanded',!foldersHidden);$('#toggle-folders').textContent=foldersHidden?'フォルダ ▸':'フォルダ ▾';};
$('#upload-button').onclick=()=>$('#upload-input').click();
$('#upload-input').onchange=safe(async e=>{const files=[...e.target.files];e.target.value='';await uploadFiles(files,state.folder||'');});

async function uploadFiles(files,folder){
  if(uploading){toast('アップロード完了までお待ちください。');return;}
  if(!files.length)return;
  if(files.length>200){toast('一度にアップロードできるのは200枚までです。');return;}
  uploading=true;$('#upload-button').disabled=true;$('#upload-status').hidden=false;
  const failures=[];let succeeded=0;
  try{
    for(let i=0;i<files.length;i++){
      const file=files[i];$('#upload-status').textContent=`アップロード中 ${i+1} / ${files.length}：${file.name}`;
      try{
        if(file.size>64*1024*1024)throw new Error('1枚64MiBまでです。');
        if(!/\.(jpe?g|png|webp|gif|avif|bmp|tiff?)$/i.test(file.name))throw new Error('対応していない形式です。');
        await new Promise((resolve,reject)=>{
          const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload?'+new URLSearchParams({folder,name:file.name}));
          xhr.setRequestHeader('X-Luma-Request','1');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=130000;
          xhr.upload.onprogress=e=>{if(e.lengthComputable)$('#upload-status').textContent=`${i+1} / ${files.length}：${file.name} (${Math.round(e.loaded/e.total*100)}%)`;};
          xhr.onload=()=>{let response;try{response=JSON.parse(xhr.responseText);}catch{response={};}xhr.status>=200&&xhr.status<300?resolve():reject(new Error(typeof response.detail==='string'?response.detail:'アップロードに失敗しました。'));};
          xhr.onerror=()=>reject(new Error('通信に失敗しました。'));xhr.ontimeout=()=>reject(new Error('タイムアウトしました。'));xhr.send(file);
        });succeeded++;
      }catch(e){failures.push(`${file.name}：${e.message}`);}
    }
    await refreshSummary();await loadImages(true);
    $('#upload-status').textContent=`${succeeded}枚をアップロードしました。${failures.length?` ${failures.length}枚は失敗しました。`:''}`;
    if(failures.length){modal('アップロード結果',`<p>${succeeded}枚を保存、${failures.length}枚は保存できませんでした。</p><ul class="upload-errors">${failures.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>`);}else{toast(`${succeeded}枚をアップロードしました。`);}
  }finally{uploading=false;$('#upload-button').disabled=false;}
}
document.addEventListener('dragstart',e=>{
  const folder=e.target.closest('[data-drag-folder]'),card=e.target.closest('.image-card');
  if(folder)dragPayload={folder:folder.dataset.dragFolder};
  else if(card)dragPayload={ids:state.selected.has(card.dataset.id)?[...state.selected]:[card.dataset.id]};
  else return;
  e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('application/x-luma',JSON.stringify(dragPayload));
});
function clearDrag(){dragPayload=null;$$('.drop-target').forEach(el=>el.classList.remove('drop-target'));$('#app').classList.remove('external-drag');}
document.addEventListener('dragend',clearDrag);
document.addEventListener('dragover',e=>{
  if($('#app').hidden||$('#modal').open||$('#viewer').open)return;
  const external=[...e.dataTransfer.types].includes('Files');
  const target=e.target.closest('[data-drop-folder]');
  if(!external&&!dragPayload)return;
  e.preventDefault();e.dataTransfer.dropEffect=external?'copy':target?'move':'none';
  $$('.drop-target').forEach(el=>el.classList.remove('drop-target'));if(target)target.classList.add('drop-target');
  $('#app').classList.toggle('external-drag',external);
});
document.addEventListener('dragleave',e=>{if(!e.relatedTarget){$('#app').classList.remove('external-drag');$$('.drop-target').forEach(el=>el.classList.remove('drop-target'));}});
document.addEventListener('drop',safe(async e=>{
  const external=[...e.dataTransfer.types].includes('Files');
  if(external)e.preventDefault();
  if($('#app').hidden||$('#modal').open||$('#viewer').open){clearDrag();return;}
  const target=e.target.closest('[data-drop-folder]'),payload=dragPayload;
  if(!external&&!payload)return;e.preventDefault();
  const destination=target?target.dataset.dropFolder:(state.folder||'');
  const files=[...e.dataTransfer.files];
  const containsDirectory=[...e.dataTransfer.items].some(item=>item.webkitGetAsEntry?.()?.isDirectory);
  clearDrag();
  if(external){if(containsDirectory){toast('ブラウザへの追加は画像ファイルを選択してください。フォルダごとの追加は公開用フォルダへコピーできます。');return;}await uploadFiles(files,destination);return;}
  if(!target)return;
  if(payload.folder){const result=await api('/folders/move','POST',{source:payload.folder,parent:destination});if(state.folder===payload.folder||state.folder?.startsWith(payload.folder+'/'))state.folder=result.path+state.folder.slice(payload.folder.length);toast('フォルダを移動しました。');}
  else{
    const result=await api('/batch','POST',{ids:payload.ids,folder:destination});const failures=result.results.filter(r=>!r.ok);
    state.selected=new Set(failures.map(r=>r.id));state.selecting=failures.length>0;updateSelection();toast(failures.length?`${failures.length}枚を移動できませんでした。${failures[0].error}`:`${result.results.length}枚を移動しました。`);
  }
  updateHeading();await refreshSummary();await loadImages(true);
}));

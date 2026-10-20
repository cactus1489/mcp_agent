import { buildRequest, markdownToHtml } from './ui-core.mjs';

const $ = id => document.getElementById(id);
const conversations = [];
let activeChat = null;
let controller = null;
let toastTimer;

function notify(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
}

function createChat() {
  // HTTP 환경에서는 randomUUID가 없을 수 있으므로 화면 내부용 ID를 생성한다.
  const id = globalThis.crypto?.randomUUID?.() ?? `chat-${Date.now()}-${conversations.length}`;
  activeChat = { id, messages: [], createdAt: new Date() };
  conversations.unshift(activeChat);
}

function renderRecent() {
  const entries = conversations.filter(chat => chat.messages.length);
  $('recent-count').textContent = entries.length ? String(entries.length) : '';
  $('empty-history').hidden = Boolean(entries.length);
  $('recent-questions').replaceChildren();
  for (const chat of entries) {
    const button = document.createElement('button'); button.className = 'recent-item';
    const icon = document.createElement('span'); icon.className = 'recent-icon'; icon.textContent = '↗';
    const text = document.createElement('span'); text.className = 'recent-text';
    const title = document.createElement('strong'); title.textContent = chat.messages.find(message => message.role === 'user')?.content ?? '새 대화';
    const date = document.createElement('small'); date.textContent = chat.createdAt.toLocaleString('ko-KR', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    text.append(title, date); const arrow = document.createElement('span'); arrow.className = 'recent-arrow'; arrow.textContent = '›';
    button.append(icon, text, arrow);
    button.addEventListener('click', () => { if (controller) return; activeChat = chat; renderConversation(); window.scrollTo({ top: document.body.scrollHeight }); $('question').focus(); });
    $('recent-questions').append(button);
  }
}

function assistantCard(message) {
  const card = document.createElement('article'); card.className = 'assistant-message';
  const heading = document.createElement('div'); heading.className = 'assistant-heading';
  const avatar = document.createElement('img'); avatar.src = '/ui/assets/images/ai-profile.png'; avatar.alt = ''; avatar.width = 38; avatar.height = 38;
  const name = document.createElement('span'); name.textContent = 'AI 이삭이'; heading.append(avatar, name); card.append(heading);
  if (message.pending) {
    const pending = document.createElement('div'); pending.setAttribute('role', 'status'); pending.className = 'loading-step';
    const spinner = document.createElement('span'); spinner.className = 'spinner'; spinner.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span'); label.textContent = '질문에 대한 답변을 준비하고 있습니다.';
    pending.append(spinner, label); card.append(pending); return card;
  }
  const body = document.createElement('div'); body.className = 'answer-body';
  if (message.error) { body.classList.add('failed-message'); body.textContent = message.content; }
  else { body.innerHTML = markdownToHtml(message.content); }
  card.append(body);
  if (!message.error) {
    const actions = document.createElement('div'); actions.className = 'answer-actions';
    const copy = document.createElement('button'); copy.className = 'copy-button'; copy.textContent = '답변 복사';
    copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(message.content); notify('답변을 복사했습니다.'); } catch { notify('복사하지 못했습니다. 답변을 선택해서 복사해주세요.'); } });
    const time = document.createElement('time'); time.textContent = '답변 완료'; actions.append(copy, time); card.append(actions);
  }
  return card;
}

function renderConversation(pending = false) {
  const messages = activeChat?.messages ?? [];
  $('welcome').hidden = Boolean(messages.length);
  $('conversation').hidden = !messages.length;
  $('conversation').replaceChildren();
  for (const message of messages) {
    if (message.role === 'user') { const bubble = document.createElement('div'); bubble.className = 'user-message'; bubble.textContent = message.content; $('conversation').append(bubble); }
    else $('conversation').append(assistantCard(message));
  }
  if (pending) $('conversation').append(assistantCard({ pending: true }));
  renderRecent();
}

function setBusy(busy) {
  $('send-button').disabled = busy; $('question').disabled = busy; $('new-chat').disabled = busy;
  $('cancel-request').hidden = !busy;
  $('connection-label').textContent = busy ? '답변을 준비하고 있습니다' : 'AI 이삭이에게 질문하세요';
}

$('chat-form').addEventListener('submit', async event => {
  event.preventDefault(); const question = $('question').value.trim();
  if (!question || controller) return;
  if (!activeChat) createChat();
  const request = buildRequest(activeChat.messages, question, $('include-history').checked);
  activeChat.messages.push({ role: 'user', content: question });
  $('question').value = ''; $('question').style.height = '';
  controller = new AbortController(); let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller?.abort(); }, 190000);
  setBusy(true); renderConversation(true); window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  try {
    const response = await fetch('/ui/chat', { method: 'POST', headers: { 'Content-Type': 'application/json; charset=utf-8' }, body: JSON.stringify({ request }), signal: controller.signal });
    if (response.status === 401) throw new Error('서버 인증 설정을 확인해주세요.');
    if (response.status === 403) throw new Error('자동 연결은 로컬 UI에서만 가능합니다. http://localhost:8000/ui로 접속해주세요.');
    if (!response.ok) throw new Error(`서버 요청에 실패했습니다. (HTTP ${response.status})`);
    const result = await response.json();
    if (typeof result.answer !== 'string') throw new Error('서버 응답에 답변이 없습니다.');
    activeChat.messages.push({ role: 'assistant', content: result.answer, error: result.status === 'FAILED' });
  } catch (error) {
    const content = error.name === 'AbortError' ? (timedOut ? '응답 대기 시간이 초과되었습니다. 잠시 후 다시 질문해주세요.' : '응답 대기를 중단했습니다. 서버의 처리는 계속될 수 있습니다.') : error instanceof TypeError ? '서버에 연결하지 못했습니다. MCP Agent가 실행 중인지 확인해주세요.' : error.message;
    activeChat.messages.push({ role: 'assistant', content, error: true });
  } finally {
    clearTimeout(timeout); controller = null; setBusy(false); renderConversation();
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); $('question').focus();
  }
});

$('question').addEventListener('input', () => { $('question').style.height = 'auto'; $('question').style.height = `${Math.min($('question').scrollHeight, 124)}px`; });
$('question').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } });
$('new-chat').addEventListener('click', () => { if (controller) return; activeChat = null; $('question').value = ''; $('question').style.height = ''; renderConversation(); window.scrollTo({ top: 0 }); $('question').focus(); });
$('cancel-request').addEventListener('click', () => controller?.abort());
$('settings-button').addEventListener('click', () => $('settings-dialog').showModal());
$('close-settings').addEventListener('click', () => $('settings-dialog').close());
$('settings-form').addEventListener('submit', event => { event.preventDefault(); $('settings-dialog').close(); notify('대화 설정을 적용했습니다.'); $('question').focus(); });
renderRecent();

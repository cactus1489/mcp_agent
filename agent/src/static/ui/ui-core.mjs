export function buildRequest(messages, question, includeHistory = true) {
  const current = question.trim();
  const history = messages.filter(message => !message.error && message.content).slice(-6);
  if (!includeHistory || !history.length) return current;
  const previous = history.map(message => `${message.role === 'user' ? '사용자' : 'AI 이삭이'}: ${message.content.slice(0, 3000)}`).join('\n\n');
  return `다음은 같은 사용자의 이전 대화입니다. 이전 대화의 작물·연도·지역 등을 참고하여 현재 질문에 답하세요.\n\n[이전 대화]\n${previous}\n\n[현재 질문]\n${current}`;
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
}

function inline(value) {
  let text = escapeHtml(value);
  const tokens = [];
  const token = html => { tokens.push(html); return `\u0000${tokens.length - 1}\u0000`; };
  text = text.replace(/`([^`]+)`/g, (_, code) => token(`<code>${code}</code>`));
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, href) => token(`<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`));
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => tokens[Number(index)] ?? '');
}

export function markdownToHtml(value) {
  const lines = String(value).replace(/\u0000/g, '').replace(/\r\n/g, '\n').split('\n');
  const result = [];
  let paragraph = [];
  let listType = '';
  const flush = () => { if (paragraph.length) { result.push(`<p>${paragraph.map(inline).join('<br>')}</p>`); paragraph = []; } };
  const closeList = () => { if (listType) { result.push(`</${listType}>`); listType = ''; } };
  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line.startsWith('```')) {
      flush(); closeList(); const code = [];
      while (++index < lines.length && !lines[index].startsWith('```')) code.push(lines[index]);
      result.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`); continue;
    }
    if (line.includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      flush(); closeList();
      result.push(`<div class="table-scroll"><table><thead><tr>${cells(line).map(cell => `<th>${inline(cell)}</th>`).join('')}</tr></thead><tbody>`);
      index++;
      while (index + 1 < lines.length && lines[index + 1].includes('|') && lines[index + 1].trim()) {
        index++; result.push(`<tr>${cells(lines[index]).map(cell => `<td>${inline(cell)}</td>`).join('')}</tr>`);
      }
      result.push('</tbody></table></div>'); continue;
    }
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    const item = line.match(/^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$/);
    if (heading) { flush(); closeList(); result.push(`<h${line.startsWith('###') ? 3 : 2}>${inline(heading[1])}</h${line.startsWith('###') ? 3 : 2}>`); }
    else if (item) {
      flush(); const type = /^\s*\d/.test(line) ? 'ol' : 'ul';
      if (listType !== type) { closeList(); listType = type; result.push(`<${type}>`); }
      result.push(`<li>${inline(item[1])}</li>`);
    }
    else if (!line.trim()) { flush(); closeList(); }
    else { closeList(); paragraph.push(line); }
  }
  flush(); closeList(); return result.join('\n');
}

const $ = s => document.querySelector(s);
function setMsg(t, ok) { const m = $('#msg'); m.textContent = t; m.className = ok ? 'ok' : 'err'; }

async function load() {
  try {
    const info = await (await fetch('/api/license/info')).json();
    $('#mc').textContent = info.machine_code;
    if (info.activated) { location.href = '/'; return; }
  } catch (e) { setMsg('读取授权状态失败：' + e.message, false); }
}
function copyMc() {
  const t = $('#mc').textContent;
  navigator.clipboard.writeText(t).then(() => setMsg('机器码已复制', true)).catch(() => setMsg('复制失败，请手动选择复制', false));
}
$('#btnAct').onclick = async () => {
  const key = $('#key').value.trim();
  if (!key) return setMsg('请先粘贴授权码', false);
  $('#btnAct').disabled = true;
  try {
    const res = await fetch('/api/license/activate', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
    const r = await res.json();
    if (!res.ok) { setMsg(r.detail || '激活失败', false); $('#btnAct').disabled = false; return; }
    setMsg('激活成功，正在进入系统…', true);
    setTimeout(() => location.href = '/', 900);
  } catch (e) {
    setMsg('激活请求失败：' + e.message, false);
    $('#btnAct').disabled = false;
  }
};
$('#btnCopyMc').addEventListener('click', copyMc);
load();

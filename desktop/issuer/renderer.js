'use strict';
const $ = id => document.getElementById(id);
let loaded = false, busy = false;
const date = new Date();
const localDate = value => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
$('expires').min = localDate(date); date.setFullYear(date.getFullYear() + 1); $('expires').value = localDate(date);
function controls() {
  document.querySelectorAll('button').forEach(button => { button.disabled = busy || (button.hasAttribute('data-key') && !loaded); });
  $('copy-code').disabled = busy || !$('code').value;
  $('expires').disabled = busy || $('permanent').checked;
  for (const id of ['passphrase', 'machine', 'permanent']) $(id).disabled = busy;
}
function feedback(message, error = false) { $('feedback').textContent = message; $('feedback').classList.toggle('error', error); }
function apply(result) {
  if (typeof result.loaded === 'boolean') {
    loaded = result.loaded; $('key-status').textContent = loaded ? '已加载' : '未加载'; $('key-status').classList.toggle('loaded', loaded);
    $('fingerprint').textContent = result.fingerprint || '—'; $('public-key').value = result.publicKey || '';
  }
}
async function action(name, input, success) {
  busy = true; controls();
  try {
    const result = await window.issuer[name](input); apply(result);
    if (result.cancelled) feedback('已取消，未保存文件。');
    else if (!result.ok) feedback(result.error || '操作未完成，请重试。', true);
    else { if (result.code) $('code').value = result.code; feedback(success); }
  } catch { feedback('操作未完成，请重试。', true); }
  finally { busy = false; controls(); }
}
for (const [id, name] of [['create', 'createKeys'], ['load', 'loadKey']]) {
  $(id).addEventListener('click', async () => {
    const passphrase = $('passphrase').value; $('passphrase').value = ''; $('code').value = '';
    await action(name, passphrase, id === 'create' ? '密钥对已保存。请安全保管私钥与口令。' : '签发私钥已加载。');
  });
}
$('issue').addEventListener('click', () => { $('code').value = ''; void action('issue', { machine: $('machine').value, expiry: $('permanent').checked ? '0' : $('expires').value }, '授权码已生成。请复制完整内容交给用户。'); });
$('export').addEventListener('click', () => void action('exportPublic', undefined, '验证公钥已保存。'));
$('copy-public').addEventListener('click', () => void action('copyCode', 'public', '验证公钥已复制。'));
$('copy-code').addEventListener('click', () => void action('copyCode', 'code', '授权码已复制。'));
$('clear').addEventListener('click', () => { $('code').value = ''; $('passphrase').value = ''; void action('clear', undefined, '内存中的签发密钥已清除。'); });
for (const id of ['machine', 'expires', 'permanent']) $(id).addEventListener('input', () => { $('code').value = ''; controls(); });
window.addEventListener('pagehide', () => { $('passphrase').value = ''; });
void action('getState', undefined, '请选择签发私钥，或创建新的密钥对。');

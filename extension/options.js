const DEFAULT_API = 'https://controlcenter.krishraja.com/api/hunter/payload';
const box = document.getElementById('api');
chrome.storage.sync.get(['api']).then((s) => { box.value = (s && s.api) || DEFAULT_API; });
document.getElementById('save').addEventListener('click', async () => {
  await chrome.storage.sync.set({ api: box.value.trim() || DEFAULT_API });
  document.getElementById('said').textContent = ' saved';
});

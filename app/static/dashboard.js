const player = document.querySelector('#radio-player');
const playerName = document.querySelector('#player-name');

document.querySelectorAll('.station-play').forEach((button) => {
  button.addEventListener('click', async () => {
    if (player.src !== button.dataset.url) player.src = button.dataset.url;
    playerName.textContent = button.dataset.name;
    try { await player.play(); } catch (_) { /* Le navigateur affiche l'erreur. */ }
  });
});

const dialog = document.querySelector('#add-dialog');
document.querySelector('#show-add').addEventListener('click', () => dialog.showModal());
dialog.querySelector('.close').addEventListener('click', () => dialog.close());

document.querySelector('#add-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = form.querySelector('.form-status');
  const data = new FormData(form);
  status.textContent = 'Ajout en cours…';
  const response = await fetch('/api/stations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: data.get('name'),
      stream_url: data.get('stream_url'),
      enabled: data.get('enabled') === 'on',
    }),
  });
  if (!response.ok) {
    const body = await response.json();
    status.textContent = body.detail || "Impossible d'ajouter cette radio.";
    return;
  }
  status.textContent = 'Radio ajoutée. Le collecteur va la prendre en charge.';
  window.setTimeout(() => window.location.reload(), 700);
});

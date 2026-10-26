const openButton = document.querySelector('#open-locker-button');
const openMessage = document.querySelector('#open-locker-message');
openButton.addEventListener('click', async () => {
    if (openButton.disabled) return;
    openButton.disabled = true;
    openMessage.textContent = 'Sending opening request…';
    try {
        const response = await fetch(openButton.dataset.url, {
            method: 'POST', headers: {'X-PayBox-Request': '1'},
            signal: AbortSignal.timeout(8000),
        });
        const result = await response.json();
        openMessage.textContent = response.ok
            ? 'Opening requested. Open the door when the latch moves, then close it to relock. Refresh to check the door.'
            : (typeof result.detail === 'string' ? result.detail : 'Request failed. Refresh to try again.');
    } catch {
        openMessage.textContent = 'Delivery is uncertain. Check the locker, wait 10 seconds, then refresh. No automatic retry was sent.';
    }
});

const openButton = document.querySelector('#open-locker-button');
const finishButton = document.querySelector('#finish-rental-button');
const openMessage = document.querySelector('#open-locker-message');

async function sendCommand(button, finish) {
    if (button.disabled) return;
    openButton.disabled = true;
    finishButton.disabled = true;
    openMessage.textContent = 'Sending opening request…';
    try {
        const headers = {'X-PayBox-Request': '1'};
        if (finish) headers['X-PayBox-Collect-All'] = 'yes';
        const response = await fetch(button.dataset.url, {
            method: 'POST', headers, signal: AbortSignal.timeout(8000),
        });
        const result = await response.json();
        openMessage.textContent = response.ok
            ? (finish
                ? 'Collection requested. When the latch moves, open the door, collect everything and close it. Refresh to check completion.'
                : 'Opening requested. Open the door when the latch moves, then close it to relock. Refresh to check the door.')
            : (typeof result.detail === 'string' ? result.detail : 'Request failed. Refresh to try again.');
    } catch {
        openMessage.textContent = 'Delivery is uncertain. Check the locker, wait 10 seconds, then refresh. No automatic retry was sent.';
    }
}

openButton.addEventListener('click', () => sendCommand(openButton, false));
const finishDialog = document.querySelector('#finish-dialog');
const cancelFinish = document.querySelector('#cancel-finish');
const confirmFinish = document.querySelector('#confirm-finish');
finishButton.addEventListener('click', () => {
    if (!finishButton.disabled) finishDialog.showModal();
});
cancelFinish.addEventListener('click', () => finishDialog.close());
confirmFinish.addEventListener('click', () => {
    finishDialog.close();
    sendCommand(finishButton, true);
});
finishDialog.addEventListener('click', (event) => {
    const bounds = finishDialog.getBoundingClientRect();
    if (event.target === finishDialog && (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom)) finishDialog.close();
});

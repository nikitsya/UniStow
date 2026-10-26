window.initialiseLockerActions = function () {
const openButton = document.querySelector('#open-locker-button');
const finishButton = document.querySelector('#finish-rental-button');
if (!openButton || openButton.dataset.bound) return;
openButton.dataset.bound = 'true';
const openMessage = document.querySelector('#open-locker-message');

async function sendCommand(button, finish) {
    if (button.disabled) return;
    window.lockerBusy = true;
    window.lockerCooldown = Date.now() + 12000;
    openButton.disabled = true;
    finishButton.disabled = true;
    openMessage.dataset.commandMessage = 'true';
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
                ? 'Collection requested. When the latch moves, open the door, collect everything and close it. The status will update automatically.'
                : 'Opening requested. Open the door when the latch moves, then close it to relock. The door status will update automatically.')
            : (typeof result.detail === 'string' ? result.detail : 'Request failed. Please try again shortly.');
    } catch {
        openMessage.textContent = 'Delivery is uncertain. Check the locker, wait a few seconds before trying again. No automatic retry was sent.';
    } finally {
        window.lockerBusy = false;
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

};
window.initialiseLockerActions();

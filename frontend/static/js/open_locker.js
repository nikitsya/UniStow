const openButton = document.querySelector('#open-locker-button');
const finishButton = document.querySelector('#finish-rental-button');
const openMessage = document.querySelector('#open-locker-message');

async function sendCommand(button, finish) {
    if (button.disabled) return;
    if (finish && !window.confirm('Finish your rental? Collect all your belongings, then close the door. Your rental ends after the door has opened and closed.')) return;
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
finishButton.addEventListener('click', () => sendCommand(finishButton, true));

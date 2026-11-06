window.initialiseLockerActions = function () {
    const openButton = document.querySelector('#open-locker-button');
    const finishButton = document.querySelector('#finish-rental-button');
    if (!openButton || openButton.dataset.bound) return;
    openButton.dataset.bound = 'true';
    const notice = document.querySelector('#active-rental-notice');

    async function sendCommand(button, finish) {
        if (button.disabled) return;
        window.lockerBusy = true;
        window.lockerCooldown = Date.now() + 12000;
        openButton.disabled = true;
        finishButton.disabled = true;
        notice.dataset.commandMessage = 'true';
        notice.textContent = finish ? notice.dataset.collectionNotice : 'Unlocking…';
        try {
            const headers = {'X-UniStow-Request': '1'};
            if (finish) headers['X-UniStow-Collect-All'] = 'yes';
            const response = await fetch(button.dataset.url, {
                method: 'POST', headers, signal: AbortSignal.timeout(8000),
            });
            const result = await response.json();
            if (response.ok && result.status === 'test_acknowledged') {
                notice.textContent = 'Controller acknowledged the test. The latch stayed in place.';
                return;
            }
            if (finish && response.ok) return;

            notice.textContent = response.ok
                ? 'Open the door once it unlocks.'
                : (typeof result.detail === 'string' ? result.detail : 'Request failed. Please try again shortly.');
        } catch {
            notice.textContent = 'Delivery is uncertain. Check the locker, wait a few seconds before trying again. No automatic retry was sent.';
        } finally {
            window.lockerBusy = false;
            document.dispatchEvent(new Event('rental-updated'));
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

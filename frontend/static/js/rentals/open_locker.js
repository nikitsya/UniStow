window.initialiseLockerActions = function () {
    const openButton = document.querySelector('#open-locker-button');
    const finishButton = document.querySelector('#finish-rental-button');
    if (!openButton || openButton.dataset.bound) return;
    openButton.dataset.bound = 'true';
    const notice = document.querySelector('#active-rental-notice');

    function showUnlockWarning() {
        notice.dataset.uncertain = 'true';
        notice.textContent = 'We couldn’t confirm whether the locker opened. Check the door before trying again.';
    }

    async function sendCommand(button, finish) {
        if (button.disabled) return;
        delete notice.dataset.uncertain;
        window.lockerBusy = true;
        window.lockerCooldown = Date.now() + 12000;
        openButton.disabled = true;
        finishButton.disabled = true;
        notice.classList.remove('unlock-warning');
        notice.dataset.commandMessage = 'true';
        notice.textContent = finish ? notice.dataset.collectionNotice : 'Unlocking…';
        try {
            const headers = {'X-UniStow-Request': '1'};
            if (finish) headers['X-UniStow-Collect-All'] = 'yes';
            const response = await fetch(button.dataset.url, {
                method: 'POST', headers, signal: AbortSignal.timeout(8000),
            });
            const result = await response.json();
            if (response.status >= 500) {
                showUnlockWarning();
                return;
            }
            if (response.ok && result.status === 'test_acknowledged') {
                notice.textContent = 'Controller acknowledged the test. The latch stayed in place.';
                return;
            }
            if (finish && response.ok) return;

            notice.textContent = response.ok
                ? 'Open the door once it unlocks.'
                : (response.status === 401 ? 'Please sign in again to use your locker.'
                    : 'Your locker is unavailable right now. Check the door and try again shortly.');
        } catch {
            showUnlockWarning();
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

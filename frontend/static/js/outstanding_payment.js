window.initialiseOutstandingPayment = function () {
    const button = document.querySelector('#pay-outstanding-button');
    if (!button || button.dataset.bound) return;
    button.dataset.bound = 'true';
    const notice = document.querySelector('#active-rental-notice, #completed-rental-notice');

    button.addEventListener('click', async () => {
        if (button.disabled || window.paymentBusy || window.rentalUnavailable) return;
        window.paymentBusy = true;
        button.disabled = true;
        delete notice.dataset.paymentMessageUntil;
        notice.textContent = 'Opening secure payment…';
        try {
            const response = await fetch(button.dataset.checkoutUrl, {
                method: 'POST', headers: {'X-UniStow-Request': '1'}, signal: AbortSignal.timeout(20000),
            });
            if (response.status === 401) {
                window.location.assign(button.dataset.loginUrl);
                return;
            }
            const result = await response.json();
            if (response.ok) {
                window.location.assign(result.url);
                return;
            }
            notice.textContent = 'We couldn’t open payment. Please refresh the page and try again.';
        } catch {
            notice.textContent = 'We couldn’t connect to payment. Please try again shortly.';
        } finally {
            notice.dataset.paymentMessageUntil = String(Date.now() + 10000);
            window.paymentBusy = false;
            button.disabled = Boolean(window.rentalUnavailable);
            document.dispatchEvent(new Event('rental-updated'));
        }
    });

    window.checkOutstandingPayment = async function () {
        const current = document.querySelector('#pay-outstanding-button');
        if (!current || window.paymentBusy) return;
        try {
            await fetch(current.dataset.reconcileUrl, {
                method: 'POST', headers: {'X-UniStow-Request': '1'}, signal: AbortSignal.timeout(8000),
            });
        } catch {
            // A later refresh retries verification, never payment creation.
        }
    };
};
window.initialiseOutstandingPayment();

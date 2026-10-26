window.initialisePayment = function () {
const payButton = document.querySelector('#pay-button');
if (!payButton || payButton.dataset.bound) return;
payButton.dataset.bound = 'true';
const paymentMessage = document.querySelector('#payment-message');
payButton.addEventListener('click', async () => {
    if (payButton.disabled) return;
    const label = payButton.textContent;
    window.paymentBusy = true;
    payButton.disabled = true;
    payButton.textContent = 'Opening Stripe…';
    paymentMessage.textContent = '';
    try {
        const response = await fetch(payButton.dataset.checkoutUrl, {
            method: 'POST', headers: {'X-PayBox-Request': '1'}, signal: AbortSignal.timeout(8000),
        });
        if (response.status === 401) {
            window.location.assign(payButton.dataset.loginUrl);
            return;
        }
        const result = await response.json();
        if (!response.ok) {
            paymentMessage.textContent = typeof result.detail === 'string'
                ? result.detail : 'Could not open checkout. Please try again.';
            return;
        }
        window.location.assign(result.url);
    } catch {
        paymentMessage.textContent = 'Could not connect to checkout. Please retry this reservation.';
    } finally {
        window.paymentBusy = false;
        payButton.disabled = false;
        payButton.textContent = label;
    }
});

async function checkPayment() {
    try {
        const response = await fetch(payButton.dataset.reconcileUrl, {
            method: 'POST', headers: {'X-PayBox-Request': '1'}, signal: AbortSignal.timeout(8000),
        });
        if (response.status === 401) {
            window.location.assign(payButton.dataset.loginUrl);
            return;
        }
        if (!response.ok) {
            paymentMessage.textContent = 'Payment verification is unavailable. We will check again shortly.';
            return;
        }
        const result = await response.json();
        if (result.status !== 'pending') {
            document.dispatchEvent(new Event('rental-updated'));
            return;
        }
        if (new URLSearchParams(window.location.search).has('returned')) {
            paymentMessage.textContent = 'Payment is not confirmed yet. We will check again shortly.';
        }
    } catch {
        paymentMessage.textContent = 'Could not check payment. We will check again shortly.';
    }
}

window.checkRentalPayment = checkPayment;

};
window.initialisePayment();

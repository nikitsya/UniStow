const form = document.querySelector('#rental-form');

if (form) {
    const daysInput = document.querySelector('#days');
    const help = document.querySelector('#days-help');
    const defaultHelp = help.textContent;
    const total = document.querySelector('#total');
    const confirm = document.querySelector('#confirm');
    const confirmation = document.querySelector('#confirmation');
    const consent = document.querySelector('#overdue-consent');
    const priceCents = Number(form.dataset.priceCents);
    let submitting = false;
    let reserved = false;
    const currency = new Intl.NumberFormat('en-IE', {style: 'currency', currency: 'EUR'});

    function updatePrice() {
        const days = Number(daysInput.value);
        const valid = daysInput.value !== '' && daysInput.validity.valid && Number.isInteger(days) && days >= 1 && days <= 4;
        const hasError = !valid && (daysInput.value !== '' || daysInput.validity.badInput);
        daysInput.setAttribute('aria-invalid', String(hasError));
        help.textContent = hasError ? 'Enter between 1 and 4 days' : defaultHelp;
        help.classList.toggle('error', hasError);
        total.textContent = currency.format(valid ? days * priceCents / 100 : 0);
        confirm.disabled = submitting || reserved || !valid || !consent.checked;
        if (!reserved && !submitting) confirmation.hidden = true;
        return valid;
    }

    daysInput.addEventListener('input', updatePrice);
    consent.addEventListener('change', updatePrice);
    window.addEventListener('pageshow', updatePrice);
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (submitting || reserved) return;
        if (!updatePrice() || !consent.checked) return;
        submitting = true;
        daysInput.disabled = true;
        consent.disabled = true;
        confirm.disabled = true;
        confirm.textContent = 'Reserving…';
        form.setAttribute('aria-busy', 'true');
        confirmation.classList.remove('error');
        try {
            const response = await fetch(form.action, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-PayBox-Request': '1'},
                body: JSON.stringify({
                    locker_id: Number(form.dataset.lockerId),
                    days: Number(daysInput.value), overdue_consent: consent.checked
                }),
            });
            if (response.status === 401) {
                window.location.assign(`/login?locker_id=${Number(form.dataset.lockerId)}`);
                return;
            }
            if (!response.ok) {
                confirmation.textContent = response.status === 409
                    ? 'This locker is already reserved or unavailable. Please return to the locker list.'
                    : response.status === 422
                        ? 'Choose 1–4 days and agree to the overdue terms.'
                        : 'Could not confirm your reservation. Please try again shortly.';
                confirmation.classList.add('error');
                return;
            }
            const rental = await response.json();
            reserved = true;
            daysInput.disabled = true;
            consent.disabled = true;
            total.textContent = currency.format(rental.total_price_cents / 100);
            const paymentLink = document.querySelector('#payment-link');
            paymentLink.href = `/rentals/${rental.id}`;
            window.location.assign(paymentLink.href);
        } catch {
            confirmation.classList.add('error');
            confirmation.textContent = reserved
                ? 'Your reservation is saved. Continue to payment to view your rental.'
                : 'Could not confirm the reservation. Check your connection and retry with the same number of days.';
            if (reserved) document.querySelector('#payment-link').hidden = false;
        } finally {
            submitting = false;
            daysInput.disabled = reserved;
            consent.disabled = reserved;
            form.removeAttribute('aria-busy');
            confirm.disabled = reserved || !consent.checked || !daysInput.validity.valid;
            confirm.textContent = reserved ? 'Reserved — awaiting payment' : 'Reserve locker';
            confirmation.hidden = false;
        }
    });
    updatePrice();
}

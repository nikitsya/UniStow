const form = document.querySelector('#rental-form');

if (form) {
    const daysInput = document.querySelector('#days');
    const help = document.querySelector('#days-help');
    const total = document.querySelector('#total');
    const confirm = document.querySelector('#confirm');
    const confirmation = document.querySelector('#confirmation');
    const priceCents = Number(form.dataset.priceCents);
    const currency = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR' });

    function updatePrice() {
        const days = Number(daysInput.value);
        const valid = daysInput.value !== '' && daysInput.validity.valid && Number.isInteger(days) && days >= 1 && days <= 7;
        const hasError = !valid && (daysInput.value !== '' || daysInput.validity.badInput);
        daysInput.setAttribute('aria-invalid', String(hasError));
        help.textContent = hasError ? 'Enter between 1 and 7 days' : 'Rent for up to 7 days';
        help.classList.toggle('error', hasError);
        total.textContent = currency.format(valid ? days * priceCents / 100 : 0);
        confirm.disabled = !valid;
        confirmation.hidden = true;
        return valid;
    }

    daysInput.addEventListener('input', updatePrice);
    window.addEventListener('pageshow', updatePrice);
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (!updatePrice()) return;
        confirmation.textContent = 'Demo only — no payment has been taken and no locker has been reserved.';
        confirmation.hidden = false;
    });
    updatePrice();
}

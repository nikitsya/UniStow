(() => {
    let running = false;
    let failures = 0;
    let lastPaymentCheck = 0;
    let relockGraceUntil = 0;
    let timer;
    const parser = new DOMParser();

    const paymentConfirmation = 'Payment confirmed. Your rental is active.';

    function showPaymentConfirmation() {
        const notice = document.querySelector('#active-rental-notice');
        if (!notice || notice.textContent.trim()) return;
        const key = 'unistow:payment-confirmed:' + window.location.pathname;
        try {
            if (sessionStorage.getItem(key)) return;
            sessionStorage.setItem(key, '1');
        } catch {
            // The current page can still show confirmation without browser storage.
        }
        notice.textContent = paymentConfirmation;
    }

    if (new URLSearchParams(window.location.search).get('returned') === '1') {
        showPaymentConfirmation();
    }

    function replaceIfChanged(selector, fresh) {
        const current = document.querySelector(selector);
        const next = fresh.querySelector(selector);
        if (!current || !next || current.outerHTML === next.outerHTML) return;
        const focused = current.contains(document.activeElement) ? document.activeElement : null;
        const href = focused?.getAttribute('href');
        current.replaceWith(next.cloneNode(true));
        if (href) {
            const replacement = [...document.querySelectorAll(`${selector} a`)]
                .find(link => link.getAttribute('href') === href);
            replacement?.focus({preventScroll: true});
        }
    }

    function updateAvailability(unavailable) {
        window.rentalUnavailable = unavailable;
        const notice = document.querySelector('#refresh-notice');
        const message = failures >= 2
            ? 'We can’t update this page right now. Please wait a moment and try again.' : '';
        if (notice) {
            notice.hidden = !message;
            if (notice.textContent !== message) notice.textContent = message;
        }
        for (const id of ['pay-button', 'cancel-reservation', 'confirm-cancellation',
            'open-locker-button', 'finish-rental-button', 'confirm-finish']) {
            const button = document.getElementById(id);
            if (!button) continue;
            if (unavailable) {
                if (!('beforeOutage' in button.dataset)) button.dataset.beforeOutage = String(button.disabled);
                button.disabled = true;
            } else if ('beforeOutage' in button.dataset) {
                button.disabled = button.dataset.beforeOutage === 'true';
                delete button.dataset.beforeOutage;
            }
        }
        const activeNotice = document.querySelector('#active-rental-notice, #payment-message');
        if (activeNotice) activeNotice.hidden = Boolean(message);
    }

    async function refresh() {
        clearTimeout(timer);
        if (running) return;
        if (document.hidden) return;
        running = true;
        try {
            if (document.querySelector('#pay-button') && !window.paymentBusy
                && Date.now() - lastPaymentCheck > 10000) {
                lastPaymentCheck = Date.now();
                await window.checkRentalPayment?.();
            }
            const fetchingDuringCommand = window.lockerBusy;
            const commandAtFetch = window.lockerCooldown;
            const response = await fetch(window.location.href, {
                cache: 'no-store', signal: AbortSignal.timeout(8000),
            });
            if (response.redirected && new URL(response.url).pathname === '/login') {
                window.location.assign(response.url);
                return;
            }
            if (!response.ok) throw new Error('Update unavailable');
            const fresh = parser.parseFromString(await response.text(), 'text/html');
            const pageSection = document.querySelector('#live-rental, .home-locker-grid, #live-rentals-list');
            const expected = pageSection?.id ? '#' + pageSection.id : '.home-locker-grid';
            if (!fresh.querySelector(expected)) {
                throw new Error('Page unavailable');
            }
            failures = 0;
            updateAvailability(false);
            const rental = document.querySelector('#live-rental');
            if (rental) {
                const next = fresh.querySelector('#live-rental');
                if (!next) throw new Error('Rental unavailable');
                for (const selector of ['.rental-overview', '.rental-facts', '.total']) {
                    replaceIfChanged(`#live-rental ${selector}`, fresh);
                }
                const actionsChanged = rental.dataset.status !== next.dataset.status
                    || Boolean(document.querySelector('#pay-button')) !== Boolean(next.querySelector('#pay-button'))
                    || Boolean(document.querySelector('#open-locker-button')) !== Boolean(next.querySelector('#open-locker-button'));
                if (actionsChanged && !window.paymentBusy && !window.lockerBusy) {
                    const justPaid = rental.dataset.status === 'pending' && next.dataset.status === 'active';
                    document.querySelector('#finish-dialog')?.close();
                    replaceIfChanged('#rental-actions', fresh);
                    rental.dataset.status = next.dataset.status;
                    if (justPaid) showPaymentConfirmation();
                    window.initialisePayment();
                    window.initialiseLockerActions();
                }
                const doorState = next.dataset.doorState;
                if (doorState !== 'closed' || !next.querySelector('#open-locker-button')?.disabled) {
                    relockGraceUntil = 0;
                } else if (rental.dataset.doorState === 'open') {
                    relockGraceUntil = Date.now() + 10000;
                }
                rental.dataset.doorState = doorState;
                const cooldown = window.lockerBusy || Date.now() < (window.lockerCooldown || 0);
                for (const id of ['open-locker-button', 'finish-rental-button']) {
                    const button = document.getElementById(id);
                    const updated = next.querySelector(`#${id}`);
                    if (button && updated) button.disabled = cooldown || updated.disabled;
                }
                const notice = document.querySelector('#active-rental-notice');
                const updatedNotice = next.querySelector('#active-rental-notice');
                if (notice && updatedNotice && !fetchingDuringCommand && !window.lockerBusy && commandAtFetch === window.lockerCooldown) {
                    const collecting = next.dataset.collecting === '1';
                    if (doorState === 'open' || collecting) delete notice.dataset.uncertain;
                    const keepCommand = notice.dataset.uncertain
                        || (notice.dataset.commandMessage && cooldown && doorState !== 'open' && !collecting);
                    if (!keepCommand) {
                        delete notice.dataset.commandMessage;
                        notice.classList.remove('unlock-warning');
                        const message = Date.now() < relockGraceUntil && !collecting
                            ? 'Securing your locker…' : updatedNotice.textContent;
                        const keepConfirmation = !message.trim() && notice.textContent === paymentConfirmation;
                        if (!keepConfirmation && notice.textContent !== message) notice.textContent = message;
                    }
                }
                const finish = document.querySelector('#finish-rental-button');
                const confirm = document.querySelector('#confirm-finish');
                if (finish && confirm) confirm.disabled = finish.disabled;
            } else if (document.querySelector('.home-locker-grid')) {
                if (!fresh.querySelector('.home-locker-grid')) throw new Error('Lockers unavailable');
                replaceIfChanged('.home-locker-grid', fresh);
                for (const filter of ['all', 'available']) {
                    const selector = `[data-filter="${filter}"] span`;
                    replaceIfChanged(selector, fresh);
                }
                document.querySelector('.home-locker-grid').inert = false;
                window.updateLockerFilter();
            } else {
                if (!fresh.querySelector('#live-rentals-list')) throw new Error('Rentals unavailable');
                replaceIfChanged('#live-rentals-list', fresh);
            }
        } catch {
            failures += 1;
            updateAvailability(true);
            const grid = document.querySelector('.home-locker-grid');
            if (grid) {
                grid.inert = true;
                for (const status of grid.querySelectorAll('.home-status')) {
                    if (status.textContent !== 'Checking availability…') status.textContent = 'Checking availability…';
                }
                const count = document.querySelector('[data-filter="available"] span');
                if (count) count.textContent = '—';
            }
        } finally {
            running = false;
            timer = setTimeout(refresh, 3000);
        }
    }

    document.addEventListener('visibilitychange', refresh);
    document.addEventListener('rental-updated', refresh);
    window.addEventListener('online', refresh);
    window.addEventListener('pageshow', refresh);
    refresh();
})();

(() => {
    let running = false;
    let lastPaymentCheck = 0;
    let timer;
    const parser = new DOMParser();

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
            const response = await fetch(window.location.href, {
                cache: 'no-store', signal: AbortSignal.timeout(8000),
            });
            if (response.redirected && new URL(response.url).pathname === '/login') {
                window.location.assign(response.url);
                return;
            }
            if (!response.ok) throw new Error('Update unavailable');
            const fresh = parser.parseFromString(await response.text(), 'text/html');
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
                    document.querySelector('#finish-dialog')?.close();
                    replaceIfChanged('#rental-actions', fresh);
                    rental.dataset.status = next.dataset.status;
                    window.initialisePayment();
                    window.initialiseLockerActions();
                }
                const cooldown = window.lockerBusy || Date.now() < (window.lockerCooldown || 0);
                for (const id of ['open-locker-button', 'finish-rental-button']) {
                    const button = document.getElementById(id);
                    const updated = next.querySelector(`#${id}`);
                    if (button && updated) button.disabled = cooldown || updated.disabled;
                }
                const notice = document.querySelector('#open-locker-message');
                if (notice && !notice.dataset.commandMessage) {
                    notice.textContent = next.querySelector('#open-locker-message')?.textContent || '';
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
                replaceIfChanged('#live-rentals-list', fresh);
            }
            const message = document.querySelector('#live-message');
            if (message) message.hidden = true;
        } catch {
            for (const id of ['open-locker-button', 'finish-rental-button', 'confirm-finish']) {
                const button = document.getElementById(id);
                if (button) button.disabled = true;
            }
            const grid = document.querySelector('.home-locker-grid');
            if (grid) grid.inert = true;
            const message = document.querySelector('#live-message');
            if (message) {
                message.textContent = 'Connection interrupted. Reconnecting…';
                message.hidden = false;
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

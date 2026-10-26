const filters = document.querySelector('.locker-filters');
const lockers = [...document.querySelectorAll('.home-locker-grid > li[data-status]')];
const emptyMessage = document.querySelector('.filter-empty');
filters.hidden = false;
filters.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-filter]');
    if (!button) return;
    filters.querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    lockers.forEach(locker => {
        locker.hidden = button.dataset.filter === 'available' && locker.dataset.status !== 'available';
    });
    emptyMessage.hidden = button.dataset.filter !== 'available' || lockers.some(locker => !locker.hidden);
});
const accountMenu = document.querySelector('.account-menu');
const accountSummary = accountMenu.querySelector('summary');
document.addEventListener('click', (event) => {
    if (!accountMenu.contains(event.target)) accountMenu.open = false;
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && accountMenu.open) {
        accountMenu.open = false;
        accountSummary.focus();
    }
});

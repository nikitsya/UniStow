const filters = document.querySelector('.locker-filters');
let lockers = [...document.querySelectorAll('.home-locker-grid > li[data-status]')];
const emptyMessage = document.querySelector('.filter-empty');
filters.hidden = false;
filters.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-filter]');
    if (!button) return;
    filters.querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    window.updateLockerFilter();
});
window.updateLockerFilter = function () {
    lockers = [...document.querySelectorAll('.home-locker-grid > li[data-status]')];
    const button = filters.querySelector('[aria-pressed="true"]');
    lockers.forEach(locker => {
        locker.hidden = button.dataset.filter === 'available' && locker.dataset.status !== 'available';
    });
    emptyMessage.hidden = button.dataset.filter !== 'available' || lockers.some(locker => !locker.hidden);
};

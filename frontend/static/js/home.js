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
const navLinks = [...document.querySelectorAll('.home-bottom-nav a')];
const sections = navLinks.map(link => document.querySelector(link.getAttribute('href')));

function updateNavigation() {
    const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
    let current = sections[0];
    sections.forEach(section => {
        if (section.getBoundingClientRect().top <= window.innerHeight * 0.4) current = section;
    });
    if (atBottom) current = sections[sections.length - 1];
    navLinks.forEach(link => {
        if (link.hash === `#${current.id}`) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
    });
}

window.addEventListener('scroll', updateNavigation, {passive: true});
updateNavigation();

const accountMenu = document.querySelector('.account-menu');
const accountSummary = accountMenu.querySelector('summary');
const accountNavButton = document.querySelector('#account-nav-button');
accountMenu.addEventListener('toggle', () => {
    accountNavButton.setAttribute('aria-expanded', String(accountMenu.open));
});
accountNavButton.addEventListener('click', () => {
    accountMenu.open = !accountMenu.open;
    if (accountMenu.open) accountSummary.focus();
});
document.addEventListener('click', (event) => {
    if (!accountMenu.contains(event.target) && !accountNavButton.contains(event.target)) accountMenu.open = false;
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && accountMenu.open) {
        accountMenu.open = false;
        accountSummary.focus();
    }
});

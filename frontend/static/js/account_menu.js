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

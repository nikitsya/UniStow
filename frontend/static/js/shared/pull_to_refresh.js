(() => {
    const refreshDistance = 80;
    let start = null;

    document.addEventListener('touchstart', event => {
        start = null;
        if (event.touches.length !== 1 || window.scrollY > 0 ||
            document.querySelector('dialog[open]') ||
            event.target.closest('input, textarea, select, [contenteditable="true"]')) return;

        start = event.touches[0];
    }, {passive: true});

    document.addEventListener('touchmove', event => {
        if (!start) return;
        const touch = event.touches[0];
        const dx = Math.abs(touch.clientX - start.clientX);
        const dy = touch.clientY - start.clientY;
        if (window.scrollY > 0 || dy < -10 || (dx > 10 && dx > dy)) start = null;
    }, {passive: true});

    document.addEventListener('touchend', event => {
        if (!start) return;
        const touch = event.changedTouches[0];
        const dy = touch.clientY - start.clientY;
        const dx = Math.abs(touch.clientX - start.clientX);
        start = null;
        if (window.scrollY <= 0 && dy >= refreshDistance && dy > dx) window.location.reload();
    }, {passive: true});

    document.addEventListener('touchcancel', () => {
        start = null;
    }, {passive: true});
})();

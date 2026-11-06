(() => {
    let pressedBackdrop = null;

    function backdropDialog(event) {
        const dialog = event.target;
        if (!(dialog instanceof HTMLDialogElement) || !dialog.matches(':modal')) return null;
        const bounds = dialog.getBoundingClientRect();
        const outside = event.clientX < bounds.left || event.clientX >= bounds.right
            || event.clientY < bounds.top || event.clientY >= bounds.bottom;
        return outside ? dialog : null;
    }

    document.addEventListener('pointerdown', event => {
        pressedBackdrop = event.isPrimary && event.button === 0 ? backdropDialog(event) : null;
    });
    document.addEventListener('pointercancel', () => {
        pressedBackdrop = null;
    });
    document.addEventListener('click', event => {
        const dialog = pressedBackdrop;
        pressedBackdrop = null;
        if (dialog && dialog === backdropDialog(event)) dialog.close();
    });
})();

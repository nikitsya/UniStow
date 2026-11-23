const logoutButton = document.querySelector('#logout-button');
if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
        logoutButton.disabled = true;
        const message = document.querySelector('#logout-message');
        message.textContent = '';
        try {
            const response = await fetch('/api/logout', {
                method: 'POST', headers: {'X-UniStow-Request': '1'},
            });
            if (response.ok) {
                window.location.reload();
                return;
            }
        } catch {
            // Network failures use the same feedback as unsuccessful responses.
        }
        message.textContent = 'Could not sign out. Please try again.';
        logoutButton.disabled = false;
    });
}

const logoutButton = document.querySelector('#logout-button');
if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
        logoutButton.disabled = true;
        const message = document.querySelector('#logout-message');
        message.textContent = '';
        try {
            const response = await fetch('/api/logout', {
                method: 'POST', headers: {'X-PayBox-Request': '1'},
            });
            if (!response.ok) throw new Error('Sign-out failed');
            window.location.reload();
        } catch {
            message.textContent = 'Could not sign out. Please try again.';
            logoutButton.disabled = false;
        }
    });
}

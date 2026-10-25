const loginForm = document.querySelector('#login-form');
const loginButton = document.querySelector('#login-button');
const loginMessage = document.querySelector('#login-message');

loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (loginButton.disabled) return;
    loginButton.disabled = true;
    loginButton.textContent = 'Signing in…';
    loginForm.setAttribute('aria-busy', 'true');
    loginMessage.textContent = '';
    loginMessage.classList.add('error');
    try {
        const response = await fetch(loginForm.action, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-PayBox-Request': '1'},
            body: JSON.stringify({
                email: loginForm.elements.email.value.trim(),
                password: loginForm.elements.password.value,
            }),
        });
        if (response.ok) {
            loginForm.reset();
            window.location.assign('/');
            return;
        }
        loginMessage.textContent = response.status === 401
            ? 'Incorrect email or password.'
            : response.status === 422
                ? 'Enter a valid email and your password.'
                : 'We could not sign you in. Please try again shortly.';
    } catch {
        loginMessage.textContent = 'Could not connect. Check your connection and try again.';
    } finally {
        loginForm.removeAttribute('aria-busy');
        loginButton.disabled = false;
        loginButton.textContent = 'Sign in';
    }
});

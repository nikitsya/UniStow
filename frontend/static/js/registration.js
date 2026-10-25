const registrationForm = document.querySelector('#registration-form');
const registerButton = document.querySelector('#register-button');
const registrationMessage = document.querySelector('#registration-message');

registrationForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (registerButton.disabled) return;

    registerButton.disabled = true;
    registerButton.textContent = 'Creating account…';
    registrationForm.setAttribute('aria-busy', 'true');
    registrationMessage.textContent = '';
    registrationMessage.classList.remove('error');
    let created = false;

    try {
        const response = await fetch(registrationForm.action, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                email: registrationForm.elements.email.value.trim(),
                password: registrationForm.elements.password.value,
            }),
        });

        if (response.status === 201) {
            created = true;
            registrationForm.reset();
            registrationMessage.textContent = 'Your account has been created. You can now sign in using the link below.';
        } else if (response.status === 409) {
            registrationMessage.textContent = 'This email is already registered. Please use another email.';
        } else if (response.status === 422) {
            registrationMessage.textContent = 'Enter a valid email and a password of 15–128 characters.';
        } else {
            registrationMessage.textContent = 'We could not create your account. Please try again shortly.';
        }
        registrationMessage.classList.toggle('error', !created);
    } catch {
        registrationMessage.classList.add('error');
        registrationMessage.textContent = 'Could not confirm registration. Check your connection before trying again.';
    } finally {
        registrationForm.removeAttribute('aria-busy');
        registerButton.disabled = created;
        registerButton.textContent = created ? 'Account created' : 'Create account';
    }
});

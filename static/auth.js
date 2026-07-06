function showAuthMode(mode) {
    const login = document.getElementById('login-form');
    const register = document.getElementById('register-form');
    const isLogin = mode === 'login';
    login.hidden = !isLogin;
    register.hidden = isLogin;
    document.title = `${isLogin ? 'Sign in' : 'Create account'} — Innertai`;
}

function showError(elementId, message) {
    const element = document.getElementById(elementId);
    element.textContent = message;
    element.hidden = false;
}

async function submitAuth(endpoint, payload, errorId, button) {
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = 'Please wait…';

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            throw new Error(data.message || 'Something went wrong.');
        }
        window.location.href = '/';
    } catch (error) {
        showError(errorId, error.message || 'Unable to connect. Please try again.');
        button.disabled = false;
        button.textContent = originalLabel;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-auth-mode]').forEach(button => {
        button.addEventListener('click', () => showAuthMode(button.dataset.authMode));
    });

    document.getElementById('loginForm').addEventListener('submit', event => {
        event.preventDefault();
        submitAuth('/login', {
            username: document.getElementById('log-username').value.trim(),
            password: document.getElementById('log-password').value
        }, 'error-msg', event.submitter);
    });

    document.getElementById('registerForm').addEventListener('submit', event => {
        event.preventDefault();
        submitAuth('/register', {
            username: document.getElementById('reg-username').value.trim(),
            password: document.getElementById('reg-password').value,
            phone_number: document.getElementById('reg-phone').value.trim()
        }, 'register-error-msg', event.submitter);
    });
});

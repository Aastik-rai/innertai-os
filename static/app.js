const icons = {
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>',
    repeat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7h-4V3M4 17h4v4m11.1-9a7 7 0 0 0-11.9-5L4 10m16 4-3.2 3a7 7 0 0 1-11.9-5"/></svg>',
    calendar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2Zm3-2v4m8-4v4M3 9h18"/></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
};

const screenTitles = {
    home: null,
    routines: 'Recurring routines',
    productivity: 'Productivity insights'
};

function setCurrentDate() {
    const date = new Intl.DateTimeFormat('en', {
        weekday: 'long', month: 'long', day: 'numeric'
    }).format(new Date());
    document.getElementById('currentDate').textContent = date;
}

function switchTab(tabName) {
    document.querySelectorAll('.app-screen').forEach(screen => screen.classList.remove('active'));
    document.querySelectorAll('[data-tab]').forEach(link => {
        link.classList.toggle('active', link.dataset.tab === tabName);
    });
    document.getElementById(`screen-${tabName}`).classList.add('active');
    document.getElementById('refreshButton').hidden = tabName !== 'home';
}

function createEmptyState(icon, title, detail) {
    const state = document.createElement('div');
    state.className = 'empty-state';
    state.innerHTML = `<div>${icons[icon]}<strong></strong><p></p></div>`;
    state.querySelector('strong').textContent = title;
    state.querySelector('p').textContent = detail;
    return state;
}

function createAction(icon, label, className, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `item-action ${className || ''}`.trim();
    button.setAttribute('aria-label', label);
    button.title = label;
    button.innerHTML = icons[icon];
    button.addEventListener('click', handler);
    return button;
}

function createTaskItem(task, routine = false) {
    const item = document.createElement('article');
    item.className = 'planner-item';

    const time = document.createElement('div');
    time.className = 'item-time';
    time.textContent = task.scheduled_time;

    const copy = document.createElement('div');
    copy.className = 'item-copy';
    const name = document.createElement('strong');
    name.textContent = task.task_name;
    const meta = document.createElement('span');
    meta.textContent = task.is_recurring ? 'Repeats every day' : 'One-time task';
    copy.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'item-actions';
    actions.append(createAction(
        'repeat',
        routine ? 'Remove from routines' : 'Toggle daily routine',
        task.is_recurring ? 'active' : '',
        () => toggleRecurring(task.id)
    ));
    if (!routine) {
        actions.append(createAction('check', 'Mark complete', 'complete', () => completeItem('task', task.id)));
    }

    item.append(time, copy, actions);
    return item;
}

function createReminderItem(reminder) {
    const item = document.createElement('article');
    item.className = 'planner-item reminder-item';

    const date = document.createElement('div');
    date.className = 'item-time';
    date.textContent = reminder.due_date || 'Soon';

    const copy = document.createElement('div');
    copy.className = 'item-copy';
    const name = document.createElement('strong');
    name.textContent = reminder.item_name;
    const meta = document.createElement('span');
    meta.textContent = reminder.time_context || 'Date reminder';
    copy.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'item-actions';
    actions.append(createAction('check', 'Dismiss reminder', 'complete', () => completeItem('reminder', reminder.id)));
    item.append(date, copy, actions);
    return item;
}

function fillList(container, items, builder, emptyConfig) {
    container.replaceChildren();
    if (!items.length) {
        container.append(createEmptyState(...emptyConfig));
        return;
    }
    items.forEach(item => container.append(builder(item)));
}

async function fetchTasks() {
    try {
        const response = await fetch('/tasks');
        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!response.ok) throw new Error('Could not load your tasks.');
        const data = await response.json();
        const pending = data.tasks.filter(task => task.status === 'pending');
        const routines = data.tasks.filter(task => task.is_recurring);

        document.getElementById('taskCount').textContent = pending.length;
        document.getElementById('reminderCount').textContent = data.reminders.length;
        document.getElementById('routineCount').textContent = routines.length;

        fillList(
            document.getElementById('taskList'), pending, task => createTaskItem(task),
            ['clock', 'Your schedule is clear', 'Add a task above when you’re ready.']
        );
        fillList(
            document.getElementById('remindersList'), data.reminders, createReminderItem,
            ['calendar', 'No upcoming reminders', 'Date-based reminders will appear here.']
        );
        fillList(
            document.getElementById('savedList'), routines, task => createTaskItem(task, true),
            ['repeat', 'No routines yet', 'Mark a task as recurring to save it here.']
        );
    } catch (error) {
        const status = document.getElementById('status');
        status.textContent = error.message;
        status.style.color = 'var(--danger)';
    }
}

async function runAction(endpoint) {
    const response = await fetch(endpoint, { method: 'POST' });
    if (!response.ok) throw new Error('The action could not be completed.');
    await fetchTasks();
}

async function toggleRecurring(id) {
    try { await runAction(`/toggle_recurring/${id}`); }
    catch (error) { window.alert(error.message); }
}

async function completeItem(type, id) {
    const endpoint = type === 'task' ? `/complete_task/${id}` : `/complete_reminder/${id}`;
    try { await runAction(endpoint); }
    catch (error) { window.alert(error.message); }
}

async function sendTasks() {
    const input = document.getElementById('taskInput');
    const status = document.getElementById('status');
    const button = document.getElementById('planBtn');
    const taskText = input.value.trim();

    if (!taskText) {
        status.textContent = 'Enter a task first.';
        status.style.color = 'var(--amber)';
        input.focus();
        return;
    }

    button.disabled = true;
    button.querySelector('span').textContent = 'Adding…';
    status.textContent = 'Organizing your task…';
    status.style.color = 'var(--muted)';

    try {
        const response = await fetch('/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tasks: taskText })
        });
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            throw new Error(data.message || 'The task could not be added.');
        }
        input.value = '';
        status.textContent = data.message || 'Task added.';
        status.style.color = 'var(--accent)';
        await fetchTasks();
    } catch (error) {
        status.textContent = error.message || 'Connection lost. Please try again.';
        status.style.color = 'var(--danger)';
    } finally {
        button.disabled = false;
        button.querySelector('span').textContent = 'Add to my day';
    }
}

async function fetchAnalytics() {
    const button = document.getElementById('analyzeBtn');
    const card = document.getElementById('aiInsight');
    const insight = document.getElementById('insightText');
    button.disabled = true;
    button.textContent = 'Analyzing…';

    try {
        const response = await fetch('/analyze');
        const data = await response.json();
        if (!response.ok || data.status !== 'success') throw new Error(data.message || 'Analysis unavailable.');
        document.getElementById('winRateVal').textContent = `${data.win_rate}%`;
        document.getElementById('completedVal').textContent = data.completed;
        document.getElementById('missedVal').textContent = data.missed;
        insight.textContent = data.insight;
        card.hidden = false;
    } catch (error) {
        insight.textContent = error.message;
        card.hidden = false;
    } finally {
        button.disabled = false;
        button.textContent = 'Refresh insight';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setCurrentDate();
    document.querySelectorAll('[data-tab]').forEach(button => {
        button.addEventListener('click', () => switchTab(button.dataset.tab));
    });
    document.getElementById('planBtn').addEventListener('click', sendTasks);
    document.getElementById('refreshButton').addEventListener('click', fetchTasks);
    document.getElementById('analyzeBtn').addEventListener('click', fetchAnalytics);
    document.getElementById('taskInput').addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') sendTasks();
    });
    fetchTasks();
});

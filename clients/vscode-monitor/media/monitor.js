const vscode = acquireVsCodeApi();

window.addEventListener('message', (event) => {
    const message = event.data;
    if (message.type === 'snapshot') {
        renderSnapshot(message.snapshot, message.snapshotPath);
    } else if (message.type === 'error') {
        renderError(message.message);
    }
});

function renderSnapshot(snapshot, snapshotPath) {
    const dashboard = document.getElementById('dashboard');
    const statusBadge = document.getElementById('statusBadge');
    const snapshotLabel = document.getElementById('snapshotPath');
    const status = snapshot.status || {};
    const target = snapshot.target || {};
    const threads = snapshot.threads || [];
    const registers = snapshot.registers || [];
    const stopEventHistory = snapshot.stop_event_history || [];
    const sourceContext = snapshot.source_context || [];
    const serialOutput = snapshot.serial_output || [];
    snapshotLabel.textContent = snapshotPath;
    statusBadge.textContent = status.state || 'unknown';
    statusBadge.dataset.state = status.state || 'unknown';

    dashboard.innerHTML = '';
    dashboard.append(
        createCard('Status', [
            statLine('Session', status.session_id),
            statLine('Current frame', formatFrame(status.current_frame)),
            statLine('Stop count', status.stop_event_count),
            statLine('Breakpoint count', status.breakpoint_count),
        ]),
        createCard('Target', [
            statLine('Executable', target.executable),
            statLine('GDB', target.gdb_executable),
            statLine('Remote target', target.remote_target || 'local'),
            statLine('Transport', target.remote_transport || '-'),
        ]),
        createListCard('Threads', threads.map((thread) => {
            const current = thread.current ? 'current' : 'thread';
            return `${current} ${thread.thread_id}: ${thread.state} ${formatFrame(thread.frame)}`;
        })),
        createListCard('Registers', registers.slice(0, 16).map((register) => `${register.name} = ${register.value}`)),
        createListCard('Recent stops', stopEventHistory.map((record) => {
            return `[${record.sequence}] ${record.event.reason} ${formatFrame(record.event.frame)}`;
        })),
        createListCard('Source', sourceContext),
        createListCard('Serial', serialOutput)
    );

    vscode.setState(snapshot);
}

function renderError(message) {
    const dashboard = document.getElementById('dashboard');
    const statusBadge = document.getElementById('statusBadge');
    statusBadge.textContent = 'error';
    statusBadge.dataset.state = 'error';
    dashboard.innerHTML = '';
    dashboard.append(createCard('Snapshot error', [message]));
}

function createCard(title, lines) {
    const section = document.createElement('section');
    section.className = 'card';
    const heading = document.createElement('h2');
    heading.textContent = title;
    section.appendChild(heading);
    for (const line of lines) {
        const paragraph = document.createElement('p');
        paragraph.className = 'stat-line';
        paragraph.textContent = line;
        section.appendChild(paragraph);
    }
    return section;
}

function createListCard(title, items) {
    return createCard(title, items.length > 0 ? items : ['<none>']);
}

function statLine(label, value) {
    return `${label}: ${value == null ? '-' : value}`;
}

function formatFrame(frame) {
    if (!frame) {
        return '-';
    }
    return `${frame.function} ${frame.file}:${frame.line}`;
}
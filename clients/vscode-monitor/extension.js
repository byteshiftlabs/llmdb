const fs = require('fs');
const path = require('path');
const vscode = require('vscode');

function activate(context) {
    const disposable = vscode.commands.registerCommand('llmdbMonitor.openMonitor', async () => {
        const config = vscode.workspace.getConfiguration('llmdbMonitor');
        let snapshotPath = config.get('snapshotPath', '');

        if (!snapshotPath) {
            const input = await vscode.window.showInputBox({
                prompt: 'Path to llmdb snapshot JSON file',
                placeHolder: '/tmp/llmdb-monitor.json',
                ignoreFocusOut: true,
            });
            if (!input) {
                return;
            }
            snapshotPath = input;
            await config.update('snapshotPath', input, vscode.ConfigurationTarget.Workspace);
        }

        const panel = vscode.window.createWebviewPanel(
            'llmdbMonitor',
            'llmdb Monitor',
            vscode.ViewColumn.Beside,
            { enableScripts: true }
        );

        const cssUri = panel.webview.asWebviewUri(vscode.Uri.file(path.join(context.extensionPath, 'media', 'monitor.css')));
        const jsUri = panel.webview.asWebviewUri(vscode.Uri.file(path.join(context.extensionPath, 'media', 'monitor.js')));
        const nonce = String(Date.now());
        panel.webview.html = getHtml(cssUri, jsUri, nonce);

        const pushSnapshot = async () => {
            try {
                const raw = await fs.promises.readFile(snapshotPath, 'utf8');
                const snapshot = JSON.parse(raw);
                panel.webview.postMessage({ type: 'snapshot', snapshot, snapshotPath });
            } catch (error) {
                panel.webview.postMessage({
                    type: 'error',
                    message: `Unable to load ${snapshotPath}: ${error.message}`,
                });
            }
        };

        const intervalMs = Math.max(500, Number(config.get('refreshIntervalMs', 1000)));
        const timer = setInterval(pushSnapshot, intervalMs);
        panel.onDidDispose(() => clearInterval(timer));
        await pushSnapshot();
    });

    context.subscriptions.push(disposable);
}

function deactivate() {}

function getHtml(cssUri, jsUri, nonce) {
    return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cssUri}; script-src 'nonce-${nonce}';" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="stylesheet" href="${cssUri}" />
    <title>llmdb Monitor</title>
</head>
<body>
    <div class="app-shell">
        <header class="hero">
            <div>
                <p class="eyebrow">llmdb monitor</p>
                <h1>QEMU debugging dashboard</h1>
                <p id="snapshotPath" class="subtle">Waiting for snapshot data…</p>
            </div>
            <div id="statusBadge" class="status-badge">loading</div>
        </header>
        <main id="dashboard" class="dashboard">
            <section class="card empty-state">
                <h2>No data yet</h2>
                <p>Run llmdb-monitor with --json-out and point this panel at that file.</p>
            </section>
        </main>
    </div>
    <script nonce="${nonce}" src="${jsUri}"></script>
</body>
</html>`;
}

module.exports = { activate, deactivate };
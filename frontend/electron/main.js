'use strict';

const { app, BrowserWindow, Menu, Tray, nativeImage, shell, screen, ipcMain, session } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

const isDev = process.env.NODE_ENV !== 'production';
const spawnBackend = process.env.SPAWN_BACKEND === 'true';

// 16×16 RGBA PNG — black medical cross on transparent bg.
// macOS treats it as a template image and inverts it for dark mode.
const TRAY_ICON_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAH0lEQVR42mNgGOzgPxSP' +
  'GkCiYmIx9Q0YjQUaGUB7AADOGivVWDvKGAAAAABJRU5ErkJggg==';

const POPOVER_WIDTH  = 180;
const POPOVER_HEIGHT = 152;

let mainWindow   = null;
let tray         = null;
let popoverWindow = null;
let backendProcess = null;

// ---------------------------------------------------------------------------
// Backend (optional, toggled via SPAWN_BACKEND=true)
// ---------------------------------------------------------------------------

function startBackend() {
  const backendDir = path.join(__dirname, '..', '..', 'backend');
  const venvPython = path.join(backendDir, '.venv', 'bin', 'python');

  backendProcess = spawn(
    venvPython,
    ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000'],
    { cwd: backendDir, stdio: 'inherit' }
  );

  backendProcess.on('error', (err) => {
    console.error('Failed to start backend:', err);
  });
}

// ---------------------------------------------------------------------------
// Main window
// ---------------------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:3000');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'out', 'index.html'));
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

function showWindow() {
  if (!mainWindow) {
    createWindow();
  } else {
    mainWindow.show();
    mainWindow.focus();
  }
}

function navigate(route) {
  showWindow();
  const base = isDev ? 'http://localhost:3000' : `file://${path.join(__dirname, '..', 'out')}`;
  mainWindow.loadURL(`${base}${route}`);
}

// ---------------------------------------------------------------------------
// Popover window
// ---------------------------------------------------------------------------

function createPopover() {
  popoverWindow = new BrowserWindow({
    width: POPOVER_WIDTH,
    height: POPOVER_HEIGHT,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  popoverWindow.loadFile(path.join(__dirname, 'popover.html'));

  // Hide when it loses focus (unless devtools are open)
  popoverWindow.on('blur', () => {
    if (!popoverWindow.webContents.isDevToolsOpened()) {
      popoverWindow.hide();
    }
  });

  popoverWindow.on('closed', () => { popoverWindow = null; });
}

function getPopoverPosition() {
  const trayBounds   = tray.getBounds();
  const { workArea } = screen.getDisplayNearestPoint({ x: trayBounds.x, y: trayBounds.y });

  // Center horizontally under the icon; place below it with a small gap
  let x = Math.round(trayBounds.x + trayBounds.width  / 2 - POPOVER_WIDTH  / 2);
  let y = Math.round(trayBounds.y + trayBounds.height + 4);

  // Keep within the screen work area
  x = Math.max(workArea.x, Math.min(x, workArea.x + workArea.width  - POPOVER_WIDTH));
  y = Math.max(workArea.y, Math.min(y, workArea.y + workArea.height - POPOVER_HEIGHT));

  return { x, y };
}

function togglePopover() {
  if (!popoverWindow) createPopover();

  if (popoverWindow.isVisible()) {
    popoverWindow.hide();
  } else {
    const { x, y } = getPopoverPosition();
    popoverWindow.setPosition(x, y, false);
    popoverWindow.show();
    popoverWindow.focus();
  }
}

// ---------------------------------------------------------------------------
// Tray
// ---------------------------------------------------------------------------

function createTray() {
  const img = nativeImage.createFromDataURL(`data:image/png;base64,${TRAY_ICON_B64}`);
  img.setTemplateImage(true);

  tray = new Tray(img);
  tray.setToolTip('CareLink');

  // Left-click → toggle popover
  tray.on('click', () => togglePopover());

  // Right-click → context menu with navigation shortcuts
  tray.on('right-click', () => {
    const menu = Menu.buildFromTemplate([
      { label: 'Show CareLink',         click: () => showWindow() },
      { type: 'separator' },
      { label: 'Patient Interface',     click: () => navigate('/patient') },
      { label: 'Caregiver Dashboard',   click: () => navigate('/dashboard') },
      { type: 'separator' },
      { label: 'Quit',                  click: () => app.quit() },
    ]);
    tray.popUpContextMenu(menu);
  });
}

// ---------------------------------------------------------------------------
// App menu
// ---------------------------------------------------------------------------

function buildMenu() {
  const template = [
    {
      label: 'File',
      submenu: [{ role: 'quit' }],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
      ],
    },
  ];

  if (process.platform === 'darwin') {
    template.unshift({
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    });
  }

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(() => {
  // Allow microphone access in renderer windows (needed for voice recording)
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(permission === 'media');
  });

  // Resize the popover window from the renderer
  ipcMain.on('resize-popover', (_event, height) => {
    if (popoverWindow) {
      popoverWindow.setSize(POPOVER_WIDTH, Math.max(300, Math.min(height, 560)), false);
      // Reposition so the bottom of the window doesn't go off-screen
      const { x } = getPopoverPosition();
      const trayBounds = tray.getBounds();
      const y = Math.round(trayBounds.y + trayBounds.height + 4);
      popoverWindow.setPosition(x, y, false);
    }
  });

  if (spawnBackend) startBackend();
  buildMenu();
  createWindow();
  createTray();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
});

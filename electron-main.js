"use strict";

/**
 * Desktop shell: starts the Node Express backend locally and loads it in a native window.
 * Run: npm run desktop
 */
const { app, BrowserWindow } = require("electron");
const path = require("path");
const http = require("http");
const { fork } = require("child_process");

const ROOT = path.join(__dirname);

let serverProcess = null;
let mainWindow = null;
let backendPort = null;

function getPort() {
  const p = parseInt(String(process.env.PORT || "3000").trim(), 10);
  return Number.isFinite(p) && p >= 1 && p <= 65535 ? p : 3000;
}

function waitForServer(port, timeoutMs = 45000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    function tryOnce() {
      const req = http.get(`http://127.0.0.1:${port}/`, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() - started > timeoutMs) {
          reject(new Error(`Backend did not respond on port ${port} within ${timeoutMs} ms.`));
          return;
        }
        setTimeout(tryOnce, 250);
      });
    }
    tryOnce();
  });
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const port = getPort();
    const env = {
      ...process.env,
      OPEN_BROWSER: "0",
      HOST: process.env.DESKTOP_BIND_HOST || "127.0.0.1",
      PORT: String(port)
    };

    serverProcess = fork(path.join(ROOT, "server.js"), [], {
      cwd: ROOT,
      env,
      silent: false
    });

    serverProcess.once("error", reject);

    const onEarlyExit = (code, signal) => {
      if (code !== 0 && code !== null) {
        reject(new Error(`server.js exited early with code ${code}${signal ? ` (${signal})` : ""}.`));
      }
    };
    serverProcess.once("exit", onEarlyExit);

    waitForServer(port)
      .then(() => {
        serverProcess.removeListener("exit", onEarlyExit);
        resolve(port);
      })
      .catch((err) => {
        if (serverProcess && !serverProcess.killed) {
          serverProcess.kill();
        }
        reject(err);
      });
  });
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 900,
    minHeight: 600,
    title: "V21 PreChecks Portal",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  mainWindow.loadURL(`http://127.0.0.1:${port}/`);

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function killBackend() {
  if (serverProcess && !serverProcess.killed) {
    serverProcess.kill();
  }
  serverProcess = null;
}

app.whenReady().then(async () => {
  try {
    backendPort = await startBackend();
    createWindow(backendPort);
  } catch (err) {
    console.error(err);
    killBackend();
    app.quit();
    process.exitCode = 1;
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendPort != null) {
    createWindow(backendPort);
  }
});

app.on("window-all-closed", () => {
  killBackend();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  killBackend();
});

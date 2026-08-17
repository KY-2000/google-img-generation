#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";

const require = createRequire(import.meta.url);
const SUPPORTED_IMAGE_SUFFIXES = new Set([".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"]);
const PAGE_ACTION_DELAYS = new WeakMap();
const PLATFORM_WARNING_PATTERN = /(?:\u64cd\u4f5c|\u8bf7\u6c42|\u8bbf\u95ee)(?:\u8fc7\u4e8e)?\u9891\u7e41|\u8d26\u53f7\u5f02\u5e38|\u5b58\u5728\u98ce\u9669|\u5b89\u5168\u9a8c\u8bc1|\u8bf7\u5b8c\u6210\u9a8c\u8bc1|\u6ed1\u5757\u9a8c\u8bc1|\u9a8c\u8bc1\u7801|unusual activity|too many requests|verify you are human/i;

function usage() {
  return `
Usage:
  node tools/xhs_download_playwright.mjs --board-url <url> --download-root <folder> [options]

Required:
  --board-url <url>            XHS board URL.
  --download-root <folder>     Folder where XHS-Downloader saves note folders.

Browser:
  --cdp <url>                  Connect to existing Edge/Chrome CDP endpoint, e.g. http://127.0.0.1:9222.
  --user-data-dir <path>       Launch persistent Edge profile if CDP is not used.
  --channel <name>             Playwright Chromium channel. Default: msedge.
  --headless                   Run headless. Default is headed.
  --background                 Do not bring the XHS tab to the foreground during normal page automation.

Download:
  --note-count <n>             Number of notes to select. Default: 1.
  --action-delay <ms>          Fixed cooldown after UI actions. Default/minimum: 5000.
  --navigation-cooldown <ms>   Cooldown after page navigation. Default/minimum: 10000.
  --timeout <ms>               Default: 180000.
  --run-root <path>            Default: ./final_runs/xhs_download.
  --auto-folder-permission     Windows only. Start a UI Automation helper for the first-time Edge folder permission prompts.
  --reset-folder-permission    Clear the saved XHS output-directory handle and select --download-root again.
  --folder-permission-timeout <seconds>
                               Helper timeout. Default: same as --timeout.
  --dry-run                    Validate inputs and Playwright availability without opening XHS.

Examples:
  node tools/xhs_download_playwright.mjs --board-url "https://www.xiaohongshu.com/board/..." --download-root "C:\\Users\\tanke\\OneDrive\\Desktop\\xhs-files-downloads" --note-count 1 --cdp http://127.0.0.1:9222
`.trim();
}

export function parseArgs(argv) {
  const out = {
    channel: "msedge",
    headless: false,
    noteCount: 1,
    actionDelay: 5000,
    navigationCooldown: 10000,
    timeout: 180000,
    runRoot: path.resolve("final_runs", "xhs_download"),
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`Missing value for ${arg}`);
      i += 1;
      return argv[i];
    };

    if (arg === "--help" || arg === "-h") out.help = true;
    else if (arg === "--board-url") out.boardUrl = next();
    else if (arg === "--download-root") out.downloadRoot = next();
    else if (arg === "--cdp") out.cdp = next();
    else if (arg === "--user-data-dir") out.userDataDir = next();
    else if (arg === "--channel") out.channel = next();
    else if (arg === "--headless") out.headless = true;
    else if (arg === "--background") out.background = true;
    else if (arg === "--note-count") out.noteCount = Number(next());
    else if (arg === "--action-delay") out.actionDelay = Number(next());
    else if (arg === "--navigation-cooldown") out.navigationCooldown = Number(next());
    else if (arg === "--timeout") out.timeout = Number(next());
    else if (arg === "--run-root") out.runRoot = path.resolve(next());
    else if (arg === "--auto-folder-permission") out.autoFolderPermission = true;
    else if (arg === "--reset-folder-permission") out.resetFolderPermission = true;
    else if (arg === "--folder-permission-timeout") out.folderPermissionTimeout = Number(next());
    else if (arg === "--dry-run") out.dryRun = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }

  if (out.help) return out;
  if (!out.boardUrl) throw new Error("--board-url is required");
  if (!out.downloadRoot) throw new Error("--download-root is required");
  if (!Number.isInteger(out.noteCount) || out.noteCount < 1) {
    throw new Error("--note-count must be a positive integer");
  }
  if (out.noteCount > 3) {
    throw new Error("--note-count is limited to 3 per run to reduce platform load");
  }
  if (!Number.isFinite(out.actionDelay) || out.actionDelay < 5000) {
    throw new Error("--action-delay must be at least 5000ms");
  }
  if (!Number.isFinite(out.navigationCooldown) || out.navigationCooldown < 10000) {
    throw new Error("--navigation-cooldown must be at least 10000ms");
  }
  if (!Number.isFinite(out.timeout) || out.timeout <= 0) {
    throw new Error("--timeout must be a positive number");
  }
  if (out.folderPermissionTimeout !== undefined && (!Number.isFinite(out.folderPermissionTimeout) || out.folderPermissionTimeout <= 0)) {
    throw new Error("--folder-permission-timeout must be a positive number");
  }
  if (out.resetFolderPermission && !out.autoFolderPermission) {
    throw new Error("--reset-folder-permission requires --auto-folder-permission");
  }
  out.downloadRoot = path.resolve(out.downloadRoot);
  return out;
}

function isValidXhsFolder(folder) {
  if (!fs.existsSync(folder) || !fs.statSync(folder).isDirectory()) return false;
  if (!fs.existsSync(path.join(folder, "metadata.json"))) return false;
  return fs.readdirSync(folder).some((name) => {
    const file = path.join(folder, name);
    return fs.statSync(file).isFile() && SUPPORTED_IMAGE_SUFFIXES.has(path.extname(name).toLowerCase());
  });
}

export function findValidXhsFolders(root) {
  if (!fs.existsSync(root)) throw new Error(`Download root does not exist: ${root}`);
  if (!fs.statSync(root).isDirectory()) throw new Error(`Download root is not a folder: ${root}`);
  return fs.readdirSync(root)
    .map((name) => path.join(root, name))
    .filter((folder) => fs.statSync(folder).isDirectory())
    .filter(isValidXhsFolder)
    .sort((a, b) => a.localeCompare(b));
}

function snapshotDirectory(root) {
  fs.mkdirSync(root, { recursive: true });
  const snapshot = new Map();
  for (const name of fs.readdirSync(root)) {
    const entry = path.join(root, name);
    if (fs.statSync(entry).isDirectory()) {
      snapshot.set(entry, fs.statSync(entry).mtimeMs);
    }
  }
  return snapshot;
}

export function diffDirectorySnapshot(root, beforeSnapshot) {
  return findValidXhsFolders(root)
    .filter((folder) => !beforeSnapshot.has(folder))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
}

export function changedValidFolders(root, beforeSnapshot) {
  return findValidXhsFolders(root)
    .filter((folder) => {
      const beforeMtime = beforeSnapshot.get(folder);
      return beforeMtime === undefined || fs.statSync(folder).mtimeMs > beforeMtime + 1000;
    })
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
}

export function selectDownloadedFolders(root, beforeSnapshot, noteCount) {
  const newFolders = diffDirectorySnapshot(root, beforeSnapshot);
  if (newFolders.length >= noteCount) return newFolders.slice(0, noteCount);
  return findValidXhsFolders(root)
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)
    .slice(0, noteCount);
}

function normalizeUrlForMatch(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return url.split("?")[0].split("#")[0];
  }
}

export function isPlatformWarningText(value) {
  return PLATFORM_WARNING_PATTERN.test(value || "");
}

export function shouldNavigateToBoard(currentUrl, boardUrl) {
  return normalizeUrlForMatch(currentUrl) !== normalizeUrlForMatch(boardUrl);
}

export async function pickXhsPage(context, boardUrl) {
  const pages = context.pages();
  const normalizedBoardUrl = normalizeUrlForMatch(boardUrl);

  const exactBoardPage = pages.find((page) => normalizeUrlForMatch(page.url()) === normalizedBoardUrl);
  if (exactBoardPage) return exactBoardPage;

  const anyBoardPage = pages.find((page) => /xiaohongshu\.com\/board\//i.test(page.url()));
  if (anyBoardPage) return anyBoardPage;

  const anyXhsPage = pages.find((page) => /xiaohongshu\.com/i.test(page.url()));
  if (anyXhsPage) return anyXhsPage;

  const nonHelperPage = pages.find((page) => !/^about:blank/i.test(page.url()));
  if (nonHelperPage) return nonHelperPage;

  return context.newPage();
}

function nextRunDir(runRoot) {
  fs.mkdirSync(runRoot, { recursive: true });
  const ids = fs.readdirSync(runRoot)
    .map((name) => /^run_(\d+)$/.exec(name)?.[1])
    .filter(Boolean)
    .map(Number);
  const id = ids.length ? Math.max(...ids) + 1 : 1;
  const runDir = path.join(runRoot, `run_${id}`);
  fs.mkdirSync(path.join(runDir, "screenshots"), { recursive: true });
  return runDir;
}

function makeLogger(runDir) {
  const logPath = path.join(runDir, "final_script_log.txt");
  fs.writeFileSync(logPath, "");
  let step = 0;
  return {
    write(message) {
      const line = `step ${step} action: ${message}`;
      fs.appendFileSync(logPath, `${line}\n`);
      console.log(line);
      step += 1;
    },
    note(message) {
      fs.appendFileSync(logPath, `${message}\n`);
      console.log(message);
    },
  };
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch (importError) {
    try {
      return require("playwright");
    } catch {
      throw new Error(`Could not load Playwright. Run "npm install". Original error: ${importError.message}`);
    }
  }
}

async function screenshot(page, runDir, name) {
  const file = path.join(runDir, "screenshots", `${Date.now()}_${name}.png`);
  try {
    await page.screenshot({ path: file, timeout: 5000 });
    return file;
  } catch (error) {
    const failureFile = path.join(runDir, "screenshots", `${Date.now()}_${name}_screenshot_failed.txt`);
    fs.writeFileSync(failureFile, error.stack || error.message || String(error));
    return null;
  }
}

async function text(page) {
  return page.locator("body").innerText({ timeout: 10_000 }).catch(() => "");
}

async function actionCooldown(page, delay) {
  await page.waitForTimeout(delay ?? PAGE_ACTION_DELAYS.get(page) ?? 5000);
}

async function assertNoPlatformWarning(page, runDir, logger, stage) {
  const body = await text(page);
  if (!isPlatformWarningText(body)) return;

  await screenshot(page, runDir, `platform_warning_${stage}`);
  logger.note(`PLATFORM_WARNING_DETECTED: ${stage}`);
  throw new Error("XHS displayed a platform warning or verification challenge. Automation stopped; review the page manually before any retry.");
}

async function clickByText(page, patterns, label, timeout = 15_000) {
  for (const pattern of patterns) {
    const locator = page.getByText(pattern, { exact: false });
    const count = await locator.count();
    for (let i = 0; i < count; i += 1) {
      const item = locator.nth(i);
      if (await item.isVisible().catch(() => false)) {
        await item.click({ timeout });
        await actionCooldown(page);
        return;
      }
    }
  }
  throw new Error(`Could not click ${label}`);
}

async function clickFirstVisible(page, locators, label, timeout = 15_000) {
  for (const locator of locators) {
    const count = await locator.count();
    for (let i = 0; i < count; i += 1) {
      const item = locator.nth(i);
      if (await item.isVisible().catch(() => false)) {
        await item.click({ timeout });
        await actionCooldown(page);
        return;
      }
    }
  }
  throw new Error(`Could not click ${label}`);
}

async function selectNativeOptionByText(page, optionPattern) {
  const selectCount = await page.locator("select").count();
  for (let i = 0; i < selectCount; i += 1) {
    const select = page.locator("select").nth(i);
    if (!(await select.isVisible().catch(() => false))) continue;

    const match = await select.evaluate((element, patternSource) => {
      const pattern = new RegExp(patternSource, "i");
      const option = Array.from(element.options).find((candidate) => pattern.test(candidate.textContent || ""));
      return option ? { value: option.value, text: option.textContent || "" } : null;
    }, optionPattern.source);

    if (match) {
      await select.selectOption(match.value);
      await actionCooldown(page);
      return true;
    }
  }
  return false;
}

async function clickOptionLike(page, patterns, label, timeout = 15_000) {
  const roleLocators = patterns.flatMap((pattern) => [
    page.getByRole("option", { name: pattern }),
    page.getByRole("menuitem", { name: pattern }),
    page.getByRole("button", { name: pattern }),
  ]);
  try {
    await clickFirstVisible(page, roleLocators, label, timeout);
    return true;
  } catch {
    await clickByText(page, patterns, label, timeout);
    return true;
  }
}

export function fallbackDownloaderButtonPoints(viewport) {
  const width = viewport?.width || 1280;
  const height = viewport?.height || 900;
  return [
    { x: width * 0.0275, y: height * 0.855 },
    { x: width * 0.043, y: height * 0.855 },
    { x: width * 0.0275, y: height * 0.89 },
    { x: width * 0.043, y: height * 0.89 },
    { x: width * 0.0275, y: height * 0.925 },
    { x: width * 0.043, y: height * 0.925 },
  ].map((point) => ({
    x: Math.round(Math.max(10, Math.min(width - 10, point.x))),
    y: Math.round(Math.max(10, Math.min(height - 10, point.y))),
  }));
}

async function hasDownloaderMenu(page) {
  return /Download Album All Content/i.test(await text(page));
}

async function clickLikelyDownloaderFloatingButton(page) {
  return page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const candidates = Array.from(document.querySelectorAll("button, [role='button'], div, a"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        const color = style.backgroundColor.match(/\d+/g)?.map(Number) || [];
        const [red = 0, green = 0, blue = 0, alpha = 255] = color;
        const isRed = red > 150 && red > green * 1.25 && red > blue * 1.15 && alpha !== 0;
        const hasBackgroundImage = style.backgroundImage && style.backgroundImage !== "none";
        const highZIndex = Number.parseInt(style.zIndex, 10) >= 9000;
        const isNearCircle = Math.abs(rect.width - rect.height) <= Math.max(12, rect.width * 0.25);
        const isLowerLeft = rect.x < viewportWidth * 0.15 && rect.y > viewportHeight * 0.65;
        const isVisible = rect.width >= 35 && rect.width <= 130 && rect.height >= 35 && rect.height <= 130
          && style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity || 1) > 0;
        const isFloating = style.position === "fixed" || style.position === "absolute";
        const looksLikeUserscriptIcon = hasBackgroundImage && highZIndex && style.cursor === "pointer";
        return { element, rect, isRed, isNearCircle, isLowerLeft, isVisible, isFloating, looksLikeUserscriptIcon };
      })
      .filter((item) => (item.isRed || item.looksLikeUserscriptIcon) && item.isNearCircle && item.isLowerLeft && item.isVisible && item.isFloating)
      .sort((a, b) => {
        const aScore = Number(a.element.querySelector("svg") !== null) + Number(a.rect.y > viewportHeight * 0.75);
        const bScore = Number(b.element.querySelector("svg") !== null) + Number(b.rect.y > viewportHeight * 0.75);
        return bScore - aScore;
      });

    if (!candidates.length) return null;
    const candidate = candidates[0];
    candidate.element.click();
    return {
      x: Math.round(candidate.rect.x + candidate.rect.width / 2),
      y: Math.round(candidate.rect.y + candidate.rect.height / 2),
      width: Math.round(candidate.rect.width),
      height: Math.round(candidate.rect.height),
    };
  }).catch(() => null);
}

async function writeDownloaderDiagnostics(page, runDir, logger) {
  await screenshot(page, runDir, "02_downloader_menu_failed");
  const diagnostics = await page.evaluate(() => {
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const candidates = Array.from(document.querySelectorAll("button, [role='button'], a, div, span"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return {
          tag: element.tagName,
          role: element.getAttribute("role") || "",
          aria: element.getAttribute("aria-label") || "",
          title: element.getAttribute("title") || "",
          text: (element.innerText || element.textContent || "").trim().slice(0, 120),
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          position: style.position,
          backgroundColor: style.backgroundColor,
          zIndex: style.zIndex,
        };
      })
      .filter((item) => item.width > 20 && item.height > 20)
      .filter((item) => item.position === "fixed" || item.x < 180 || item.y > viewport.height - 220)
      .slice(0, 80);
    return { url: location.href, title: document.title, viewport, candidates };
  }).catch((error) => ({ error: error.message }));
  const diagnosticPath = path.join(runDir, "xhs_downloader_menu_diagnostics.json");
  fs.writeFileSync(diagnosticPath, JSON.stringify(diagnostics, null, 2));
  logger.note(`Wrote downloader menu diagnostics: ${diagnosticPath}`);
}

async function writePageDiagnostics(page, runDir, name, logger) {
  await screenshot(page, runDir, name);
  const diagnostics = await page.evaluate(() => {
    const controls = Array.from(document.querySelectorAll("select, button, [role='button'], [role='combobox'], [role='option'], [role='menuitem'], input"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const options = element.tagName === "SELECT"
          ? Array.from(element.options).map((option) => ({ text: option.textContent || "", value: option.value }))
          : [];
        return {
          tag: element.tagName,
          type: element.getAttribute("type") || "",
          role: element.getAttribute("role") || "",
          aria: element.getAttribute("aria-label") || "",
          text: (element.innerText || element.textContent || "").trim().slice(0, 160),
          value: element.value || "",
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          options,
        };
      })
      .filter((item) => item.width > 0 && item.height > 0)
      .slice(0, 120);
    return { url: location.href, title: document.title, bodyText: document.body.innerText.slice(0, 2000), controls };
  }).catch((error) => ({ error: error.message }));
  const diagnosticPath = path.join(runDir, `${name}.json`);
  fs.writeFileSync(diagnosticPath, JSON.stringify(diagnostics, null, 2));
  logger.note(`Wrote page diagnostics: ${diagnosticPath}`);
}

async function openDownloaderMenu(page, runDir, logger) {
  logger.write("open XHS-Downloader floating book menu");
  await screenshot(page, runDir, "02_before_downloader_menu");

  if (await hasDownloaderMenu(page)) return;

  const candidates = [
    page.locator("#XHSDownloaderFloatingButton"),
    page.locator('[data-xhs-downloader-control="floating-menu-button"]'),
    page.getByRole("button", { name: /Open XHS-Downloader menu/i }),
    page.getByRole("button", { name: /小红书|book|download/i }),
    page.locator("button").filter({ hasText: /小红书|下载|book|download/i }),
    page.locator("[role=button]").filter({ hasText: /小红书|下载|book|download/i }),
  ];
  try {
    await clickFirstVisible(page, candidates, "XHS-Downloader floating button", 5000);
  } catch {
    const clickedCandidate = await clickLikelyDownloaderFloatingButton(page);
    if (clickedCandidate) {
      logger.note(`Clicked DOM-detected XHS-Downloader floating button: ${JSON.stringify(clickedCandidate)}`);
      await actionCooldown(page);
      if (await hasDownloaderMenu(page)) return;
    }

    const viewport = await page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }))
      .catch(() => page.viewportSize() || { width: 1280, height: 900 });
    const [point] = fallbackDownloaderButtonPoints(viewport);
    logger.note(`Trying one last-resort XHS-Downloader fallback click at ${point.x},${point.y}`);
    await page.mouse.click(point.x, point.y);
    await actionCooldown(page);
    if (await hasDownloaderMenu(page)) return;
  }

  await actionCooldown(page);
  if (!(await hasDownloaderMenu(page))) {
    await writeDownloaderDiagnostics(page, runDir, logger);
    throw new Error("XHS-Downloader menu did not open. Verify Tampermonkey script is active in this Edge profile.");
  }
}

async function chooseAllLoadedNotes(page, runDir, logger) {
  logger.write("choose Latest / All loaded notes filter and continue");
  await screenshot(page, runDir, "03_filter_notes");
  const body = await text(page);
  if (!/Filter Notes|Latest|All loaded notes/i.test(body)) {
    await writePageDiagnostics(page, runDir, "03_filter_notes_missing", logger);
    throw new Error("Filter Notes dialog did not appear after Download Album All Content.");
  }

  const selectedNative = await selectNativeOptionByText(page, /All loaded notes/);
  if (!selectedNative && !/All loaded notes/i.test(body)) {
    const combos = page.getByRole("combobox");
    const comboCount = await combos.count();
    let selectedCustom = false;
    for (let i = 0; i < comboCount; i += 1) {
      const combo = combos.nth(i);
      if (!(await combo.isVisible().catch(() => false))) continue;
      await combo.click().catch(() => {});
      await actionCooldown(page);
      selectedCustom = await clickOptionLike(page, [/All loaded notes/i], "All loaded notes", 5000).catch(() => false);
      if (selectedCustom) break;
    }
    if (!selectedCustom) {
      await writePageDiagnostics(page, runDir, "03_all_loaded_notes_failed", logger);
      throw new Error("Could not click All loaded notes");
    }
  }

  await clickFirstVisible(
    page,
    [page.getByRole("button", { name: /^Continue$/i }), page.locator("button").filter({ hasText: /^Continue$/i })],
    "Filter Notes Continue",
  );
}

async function selectAllContentOptions(page, logger) {
  logger.write("select all content options and continue");
  const body = await text(page);
  if (!/Images|media|Title|captions|comments/i.test(body)) {
    throw new Error("Download Content options dialog did not appear.");
  }

  const checkboxes = page.getByRole("checkbox");
  const count = await checkboxes.count();
  for (let i = 0; i < count; i += 1) {
    const checkbox = checkboxes.nth(i);
    if (await checkbox.isVisible().catch(() => false)) {
      const checked = await checkbox.isChecked().catch(() => true);
      if (!checked) {
        await checkbox.check();
        await actionCooldown(page);
      }
    }
  }

  await clickFirstVisible(
    page,
    [page.getByRole("button", { name: /^Continue$/i }), page.locator("button").filter({ hasText: /^Continue$/i })],
    "Download Content Continue",
  );
}

function startWindowsFolderPermissionHelper(options, runDir, logger) {
  if (!options.autoFolderPermission) return null;
  if (process.platform !== "win32") {
    logger.note("--auto-folder-permission is only supported on Windows; skipping helper");
    return null;
  }

  const helperPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "windows_edge_file_permission_helper.ps1");
  if (!fs.existsSync(helperPath)) {
    throw new Error(`Windows folder permission helper not found: ${helperPath}`);
  }

  const timeoutSeconds = Math.ceil(options.folderPermissionTimeout || options.timeout / 1000);
  const logPath = path.join(runDir, "windows_folder_permission_helper.log");
  fs.writeFileSync(logPath, `Node starting Windows folder permission helper at ${new Date().toISOString()}\n`);
  const args = [
    "-Sta",
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    helperPath,
    "-FolderPath",
    options.downloadRoot,
    "-TimeoutSeconds",
    String(timeoutSeconds),
    "-LogPath",
    logPath,
  ];

  logger.note(`Starting Windows folder permission helper: ${logPath}`);
  const child = spawn("powershell.exe", args, {
    stdio: ["ignore", "ignore", "pipe"],
    windowsHide: true,
  });
  logger.note(`Windows folder permission helper pid: ${child.pid || "unknown"}`);

  child.stderr?.on("data", (chunk) => {
    fs.appendFileSync(logPath, chunk);
  });
  child.on("error", (error) => {
    fs.appendFileSync(logPath, `helper process error: ${error.stack || error.message}\n`);
    logger.note(`Windows folder permission helper failed to start: ${error.message}`);
  });
  child.on("exit", (code, signal) => {
    fs.appendFileSync(logPath, `Node observed helper exit: code=${code}; signal=${signal || ""}\n`);
    logger.note(`Windows folder permission helper exited: code=${code}; signal=${signal || ""}`);
  });

  return { child, logPath };
}

function stopWindowsFolderPermissionHelper(helper, logger) {
  if (!helper?.child || helper.child.exitCode !== null || helper.child.killed) return;
  logger.note("Stopping Windows folder permission helper after XHS download stage");
  helper.child.kill();
}

async function selectNotesAndDownload(page, options, runDir, logger) {
  const noteCount = options.noteCount;
  logger.write(`select ${noteCount} notes and download selected`);
  await page.getByText(/Select Notes to Download/i).waitFor({ state: "visible", timeout: 30_000 });

  for (let selected = 0; selected < noteCount; selected += 1) {
    const selectedText = await text(page);
    if (new RegExp(`Selected:\\s*${noteCount}\\s*/`, "i").test(selectedText)) break;

    const uncheckedCircles = page.locator(".note-card, [role=option], .card").filter({ hasNotText: /selected/i });
    if (await uncheckedCircles.count()) {
      const card = uncheckedCircles.nth(selected);
      const box = await card.boundingBox();
      if (box) {
        await page.mouse.click(box.x + box.width - 24, box.y + 24);
      }
    } else {
      const viewport = page.viewportSize() || { width: 1280, height: 900 };
      await page.mouse.click(340 + selected * 260, Math.min(430, viewport.height - 300));
    }
    await actionCooldown(page);
  }

  page.once("dialog", async (dialog) => {
    await dialog.accept();
  });

  await assertNoPlatformWarning(page, runDir, logger, "before_download");
  const folderPermissionHelper = startWindowsFolderPermissionHelper(options, runDir, logger);

  await clickFirstVisible(
    page,
    [
      page.getByRole("button", { name: /Download Selected/i }),
      page.locator("button").filter({ hasText: /Download Selected/i }),
    ],
    "Download Selected",
  );

  await actionCooldown(page);
  return folderPermissionHelper;
}

async function waitForDownloadComplete(page, downloadRoot, beforeSnapshot, noteCount, timeout, runDir, logger) {
  logger.write("wait for XHS download completion");
  const start = Date.now();
  let lastBody = "";
  while (Date.now() - start < timeout) {
    await assertNoPlatformWarning(page, runDir, logger, "download_wait");
    const selected = changedValidFolders(downloadRoot, beforeSnapshot).slice(0, noteCount);
    lastBody = await text(page);
    if (selected.length >= noteCount && /Download Complete|Complete|Downloaded|0 \/ 0|下载完成/i.test(lastBody)) {
      return selected;
    }
    if (selected.length >= noteCount && !/Downloading Notes|Upload Remaining|Downloading/i.test(lastBody)) {
      return selected;
    }
    await page.waitForTimeout(3000);
  }
  throw new Error(`Timed out waiting for XHS download. Last page text excerpt: ${lastBody.slice(0, 500)}`);
}

async function connectBrowser(options, logger) {
  const { chromium } = await loadPlaywright();
  if (options.cdp) {
    logger.write(`connect to existing browser over CDP: ${options.cdp}`);
    const browser = await chromium.connectOverCDP(options.cdp);
    return { browser, context: browser.contexts()[0] || await browser.newContext(), shouldCloseContext: false };
  }

  const userDataDir = path.resolve(options.userDataDir || ".pw-edge-xhs-profile");
  logger.write(`launch persistent browser: channel=${options.channel}; userDataDir=${userDataDir}`);
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: options.channel,
    headless: options.headless,
    viewport: { width: 1280, height: 900 },
    acceptDownloads: true,
  });
  return { browser: null, context, shouldCloseContext: true };
}

async function run(options) {
  fs.mkdirSync(options.downloadRoot, { recursive: true });
  const beforeSnapshot = snapshotDirectory(options.downloadRoot);
  const runDir = nextRunDir(options.runRoot);
  const logger = makeLogger(runDir);
  fs.copyFileSync(fileURLToPath(import.meta.url), path.join(runDir, "final_script.mjs.txt"));
  logger.write(`params: boardUrl=${options.boardUrl}; downloadRoot=${options.downloadRoot}; noteCount=${options.noteCount}; actionDelay=${options.actionDelay}; navigationCooldown=${options.navigationCooldown}`);

  if (options.dryRun) {
    await loadPlaywright();
    const existingValidFolders = findValidXhsFolders(options.downloadRoot);
    logger.note(JSON.stringify({ ok: true, mode: "dry-run", existingValidFolders }, null, 2));
    return { runDir, folders: existingValidFolders };
  }

  const { context, shouldCloseContext } = await connectBrowser(options, logger);
  context.setDefaultTimeout(options.timeout);
  const page = await pickXhsPage(context, options.boardUrl);
  page.setDefaultTimeout(options.timeout);
  PAGE_ACTION_DELAYS.set(page, options.actionDelay);
  let folderPermissionHelper = null;

  try {
    if (shouldNavigateToBoard(page.url(), options.boardUrl)) {
      logger.write("open XHS board page");
      await page.goto(options.boardUrl, { waitUntil: "domcontentloaded", timeout: options.timeout });
      await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => {});
      await page.waitForTimeout(options.navigationCooldown);
    } else {
      logger.write("reuse already-open XHS board page without reloading");
    }
    await screenshot(page, runDir, "01_board");
    await assertNoPlatformWarning(page, runDir, logger, "board");

    const body = await text(page);
    if (/login|sign in|登录|验证码/i.test(body)) {
      throw new Error("XHS login or verification appears required. Log in manually in this Edge profile, then rerun.");
    }

    if (options.resetFolderPermission) {
      logger.note("RESET_FOLDER_PERMISSION: forcing XHS-Downloader to clear its saved directory handle and open the folder picker.");
      await page.evaluate(() => {
        localStorage.setItem("xhs-downloader-force-pick-output-directory", "1");
      });
    }

    await openDownloaderMenu(page, runDir, logger);
    await assertNoPlatformWarning(page, runDir, logger, "downloader_menu");
    await clickByText(page, [/Download Album All Content/i], "Download Album All Content");
    await actionCooldown(page);

    const pages = context.pages();
    const originalPage = pages.find((candidate) => candidate.url().includes("xiaohongshu.com/board")) || page;
    PAGE_ACTION_DELAYS.set(originalPage, options.actionDelay);
    if (!options.background || options.resetFolderPermission) {
      await originalPage.bringToFront();
      if (options.background && options.resetFolderPermission) {
        logger.note("BACKGROUND_MODE_NOTE: temporarily brought XHS to the foreground to reset the output folder.");
      }
    } else {
      logger.note("BACKGROUND_MODE: continuing XHS page automation without bringing the tab to the foreground.");
      if (options.autoFolderPermission) {
        logger.note("BACKGROUND_MODE_NOTE: a first-time Windows folder permission prompt may still temporarily take focus.");
      }
    }
    await chooseAllLoadedNotes(originalPage, runDir, logger);
    await assertNoPlatformWarning(originalPage, runDir, logger, "filters");
    await selectAllContentOptions(originalPage, logger);
    await assertNoPlatformWarning(originalPage, runDir, logger, "content_options");
    folderPermissionHelper = await selectNotesAndDownload(originalPage, options, runDir, logger);

    const folders = await waitForDownloadComplete(
      originalPage,
      options.downloadRoot,
      beforeSnapshot,
      options.noteCount,
      options.timeout,
      runDir,
      logger,
    );
    await screenshot(originalPage, runDir, "03_download_complete");
    logger.note(`DOWNLOADED_FOLDERS: ${JSON.stringify(folders)}`);
    logger.note(`RUN_DIR: ${runDir}`);
    logger.note("XHS_DOWNLOAD_STAGE_COMPLETE: xhs-download only downloads notes and returns downloaded folder paths.");
    return { runDir, folders };
  } finally {
    stopWindowsFolderPermissionHelper(folderPermissionHelper, logger);
    if (shouldCloseContext) {
      await context?.close().catch(() => {});
    }
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    return;
  }
  const result = await run(options);
  console.log(JSON.stringify({ ok: true, runDir: result.runDir, folders: result.folders }, null, 2));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main()
    .then(() => process.exit(0))
    .catch((error) => {
      console.error(error.stack || error.message || String(error));
      process.exit(1);
    });
}

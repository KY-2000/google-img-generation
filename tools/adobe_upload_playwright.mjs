#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function usage() {
  return `
Usage:
  node tools/adobe_upload_playwright.mjs --csv <adobe_stock_metadata.csv> [options]

Required:
  --csv <path>                 Adobe Stock CSV with Filename,Title,Keywords,Category,Releases

Image selection:
  --images <path>              Image file, image directory, or comma-separated image files.
                               Default: CSV directory, matching CSV Filename values.

Browser:
  --cdp <url>                  Connect to an already-running Edge/Chrome CDP endpoint,
                               for example http://127.0.0.1:9222.
  --user-data-dir <path>       Launch a persistent browser profile directory.
  --resume-after <count>       Skip a verified prefix of already-uploaded images.
  --channel <name>             Playwright Chromium channel. Default: msedge.
  --headless                   Run headless. Default is headed.

Adobe:
  --url <url>                  Default: https://contributor.stock.adobe.com/en/uploads
  --file-type <type>           Set Adobe file type after upload. Values: photos, illustrations.
  --mark-ai                    Check "Created using generative AI tools" after CSV import.
  --mark-fictional             Check "People and Property are fictional" if visible.
  --save-work                  Click "Save work" if it becomes enabled.

Evidence:
  --run-root <path>            Default: ./final_runs
  --timeout <ms>               Default: 120000
  --dry-run                    Validate inputs and Playwright availability without opening Adobe.

Examples:
  node tools/adobe_upload_playwright.mjs --csv adobe_upload_test_2160x3840/adobe_stock_metadata.csv --images adobe_upload_test_2160x3840 --user-data-dir .pw-edge-profile --mark-ai --save-work

  msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\edge-adobe-cdp"
  node tools/adobe_upload_playwright.mjs --csv adobe_upload_test_2160x3840/adobe_stock_metadata.csv --images adobe_upload_test_2160x3840 --cdp http://127.0.0.1:9222 --mark-ai --save-work
`.trim();
}

function parseArgs(argv) {
  const out = {
    url: "https://contributor.stock.adobe.com/en/uploads",
    channel: "msedge",
    headless: false,
    runRoot: path.resolve("final_runs"),
    timeout: 120000,
    markAi: false,
    markFictional: false,
    saveWork: false,
    resumeAfter: 0,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`Missing value for ${arg}`);
      i += 1;
      return argv[i];
    };

    if (arg === "--help" || arg === "-h") out.help = true;
    else if (arg === "--csv") out.csv = next();
    else if (arg === "--images") out.images = next();
    else if (arg === "--cdp") out.cdp = next();
    else if (arg === "--user-data-dir") out.userDataDir = next();
    else if (arg === "--resume-after") out.resumeAfter = Number(next());
    else if (arg === "--channel") out.channel = next();
    else if (arg === "--url") out.url = next();
    else if (arg === "--file-type") out.fileType = next().toLowerCase();
    else if (arg === "--run-root") out.runRoot = next();
    else if (arg === "--timeout") out.timeout = Number(next());
    else if (arg === "--headless") out.headless = true;
    else if (arg === "--mark-ai") out.markAi = true;
    else if (arg === "--mark-fictional") out.markFictional = true;
    else if (arg === "--save-work") out.saveWork = true;
    else if (arg === "--dry-run") out.dryRun = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }

  if (out.help) return out;
  if (!out.csv) throw new Error("--csv is required");
  if (!Number.isFinite(out.timeout) || out.timeout <= 0) throw new Error("--timeout must be a positive number");
  if (!Number.isInteger(out.resumeAfter) || out.resumeAfter < 0) {
    throw new Error("--resume-after must be a nonnegative integer");
  }
  if (out.fileType && !["photos", "illustrations"].includes(out.fileType)) {
    throw new Error("--file-type must be one of: photos, illustrations");
  }
  return out;
}

function parseCsvLine(line) {
  const cells = [];
  let cell = "";
  let quoted = false;

  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quoted && ch === '"' && line[i + 1] === '"') {
      cell += '"';
      i += 1;
    } else if (ch === '"') {
      quoted = !quoted;
    } else if (!quoted && ch === ",") {
      cells.push(cell);
      cell = "";
    } else {
      cell += ch;
    }
  }
  cells.push(cell);
  return cells;
}

function parseAdobeCsv(csvPath) {
  const text = fs.readFileSync(csvPath, "utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length < 2) throw new Error(`CSV must contain a header and at least one row: ${csvPath}`);

  const header = parseCsvLine(lines[0]);
  const expected = ["Filename", "Title", "Keywords", "Category", "Releases"];
  if (header.join(",") !== expected.join(",")) {
    throw new Error(`CSV header must be exactly ${expected.join(",")} but got ${header.join(",")}`);
  }

  return lines.slice(1).map((line, index) => {
    const cells = parseCsvLine(line);
    return {
      row: index + 2,
      filename: cells[0] || "",
      title: cells[1] || "",
      keywords: cells[2] || "",
      category: cells[3] || "",
      releases: cells[4] || "",
    };
  });
}

function resolveImages(imagesArg, csvDir, rows) {
  const expectedNames = rows.map((row) => row.filename).filter(Boolean);
  let candidates = [];

  if (!imagesArg) {
    candidates = expectedNames.map((name) => path.resolve(csvDir, name));
  } else if (imagesArg.includes(",")) {
    candidates = imagesArg.split(",").map((p) => path.resolve(p.trim())).filter(Boolean);
  } else {
    const resolved = path.resolve(imagesArg);
    const stat = fs.statSync(resolved);
    if (stat.isDirectory()) {
      candidates = expectedNames.map((name) => path.resolve(resolved, name));
    } else {
      candidates = [resolved];
    }
  }

  for (const file of candidates) {
    if (!fs.existsSync(file)) throw new Error(`Image file not found: ${file}`);
  }

  const basenames = new Set(candidates.map((file) => path.basename(file)));
  const missing = expectedNames.filter((name) => !basenames.has(name));
  if (missing.length) {
    throw new Error(`CSV Filename values missing from selected images: ${missing.join(", ")}`);
  }

  return candidates;
}

function resumeImagePaths(imagePaths, resumeAfter) {
  if (resumeAfter > imagePaths.length) {
    throw new Error(`--resume-after ${resumeAfter} cannot exceed the ${imagePaths.length} selected images`);
  }
  return imagePaths.slice(resumeAfter);
}

function chunkFilesBySize(files, maxBytes, getSize = (file) => fs.statSync(file).size) {
  const chunks = [];
  let current = [];
  let currentBytes = 0;

  for (const file of files) {
    const fileBytes = getSize(file);
    if (!Number.isFinite(fileBytes) || fileBytes < 0) {
      throw new Error(`Could not determine upload size for ${file}`);
    }
    if (fileBytes > maxBytes) {
      throw new Error(
        `File is too large for safe CDP transfer (${fileBytes} bytes, limit ${maxBytes}): ${file}`,
      );
    }
    if (current.length && currentBytes + fileBytes > maxBytes) {
      chunks.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(file);
    currentBytes += fileBytes;
  }

  if (current.length) chunks.push(current);
  return chunks;
}

function nextRunDir(runRoot) {
  fs.mkdirSync(runRoot, { recursive: true });
  const ids = fs.readdirSync(runRoot)
    .map((name) => /^run_(\d+)$/.exec(name)?.[1])
    .filter(Boolean)
    .map(Number);
  const next = ids.length ? Math.max(...ids) + 1 : 1;
  const runDir = path.join(runRoot, `run_${next}`);
  fs.mkdirSync(path.join(runDir, "screenshots"), { recursive: true });
  return runDir;
}

function makeLogger(runDir) {
  const logPath = path.join(runDir, "final_script_log.txt");
  fs.writeFileSync(logPath, "");
  let step = 0;

  return {
    path: logPath,
    write(message) {
      const line = `step ${step} action: ${message}\n`;
      fs.appendFileSync(logPath, line);
      console.log(line.trimEnd());
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
      const candidateDirs = [
        process.env.PLAYWRIGHT_NODE_MODULES,
        process.env.NODE_PATH,
        process.env.USERPROFILE
          ? path.join(process.env.USERPROFILE, ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules")
          : null,
        process.env.HOME
          ? path.join(process.env.HOME, ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules")
          : null,
      ]
        .filter(Boolean)
        .flatMap((value) => String(value).split(path.delimiter))
        .filter(Boolean);

      for (const dir of candidateDirs) {
        try {
          const searchRoots = [dir];
          if (path.basename(dir).toLowerCase() === "node_modules") {
            searchRoots.push(path.dirname(dir));
          }
          const resolved = require.resolve("playwright", { paths: searchRoots });
          const loaded = require(resolved);
          return loaded;
        } catch (candidateError) {
          try {
            const direct = path.join(dir, "playwright");
            if (fs.existsSync(direct)) return require(direct);
          } catch {
            // Preserve the first candidate error below.
          }
          if (!globalThis.__adobeUploadPlaywrightLoadError) {
            globalThis.__adobeUploadPlaywrightLoadError = candidateError;
          }
          // Try the next candidate module directory.
        }
      }

      const candidateErrorMessage = globalThis.__adobeUploadPlaywrightLoadError
        ? ` Candidate load error: ${globalThis.__adobeUploadPlaywrightLoadError.message}`
        : "";
      throw new Error(
        `Could not load Playwright. Install it with "npm i -D playwright", set PLAYWRIGHT_NODE_MODULES to a node_modules directory containing Playwright, or run this script with a Node runtime that has Playwright. Original error: ${importError.message}.${candidateErrorMessage}`,
      );
    }
  }
}

async function screenshot(page, runDir, name, logger) {
  const file = path.join(runDir, "screenshots", `${String(Date.now())}_${name}.png`);
  try {
    await page.screenshot({ path: file, timeout: 15000 });
    return file;
  } catch (error) {
    logger?.note(`WARNING: evidence screenshot ${name} failed: ${error.message}`);
    return null;
  }
}

async function clickFirstVisible(locator, label, timeout = 15000) {
  const count = await locator.count();
  for (let i = 0; i < count; i += 1) {
    const item = locator.nth(i);
    try {
      await item.waitFor({ state: "visible", timeout: Math.min(timeout, 3000) });
      await item.click({ timeout });
      return;
    } catch {
      // Try the next matching element.
    }
  }
  throw new Error(`No visible clickable element found for ${label}`);
}

async function setFilesFromChooser(page, clickAction, files, timeout) {
  const chooserPromise = page.waitForEvent("filechooser", { timeout });
  await clickAction();
  const chooser = await chooserPromise;
  await chooser.setFiles(files);
}

async function waitForNoUploadRemaining(page, timeout, initialFileCount, expectedFileCount) {
  const start = Date.now();
  let lastText = "";
  while (Date.now() - start < timeout) {
    lastText = await page.locator("body").innerText({ timeout: 5000 });
    const countMatch = /File types:\s*All\s*\((\d+)\)/i.exec(lastText);
    const currentFileCount = countMatch ? Number(countMatch[1]) : null;
    const uploadPending = /Uploads?\s+Remaining/i.test(lastText);
    const expectedTotal = initialFileCount + expectedFileCount;
    if (!uploadPending && currentFileCount !== null && currentFileCount >= expectedTotal) {
      return lastText;
    }
    await page.waitForTimeout(3000);
  }
  throw new Error(
    `Timed out waiting for Adobe upload queue to finish and file count to increase from ${initialFileCount} to ${initialFileCount + expectedFileCount}. Last visible text excerpt: ${lastText.slice(0, 1000)}`,
  );
}

async function waitForCsvApplication(page, rows, timeout, logger) {
  const start = Date.now();
  const firstTitle = rows[0]?.title?.trim();
  let lastText = "";

  while (Date.now() - start < timeout) {
    lastText = await page.locator("body").innerText({ timeout: 10000 });

    if (/Data from your CSV was applied to the related files/i.test(lastText)) {
      return { status: "applied-banner", text: lastText };
    }

    if (/Refresh to view changes/i.test(lastText)) {
      return { status: "refresh-available", text: lastText };
    }

    if (firstTitle && lastText.includes(firstTitle)) {
      return { status: "metadata-visible", text: lastText };
    }

    if (/Your CSV is processing/i.test(lastText)) {
      logger.note("CSV is still processing; waiting before checking again.");
    }

    if (/couldn't process|failed to process|CSV.*error|invalid CSV/i.test(lastText)) {
      throw new Error("Adobe reported a CSV processing error. Inspect the screenshot and CSV format.");
    }

    await page.waitForTimeout(5000);
  }

  throw new Error(
    `Timed out waiting for CSV metadata application. Last visible text excerpt: ${lastText.slice(0, 1000)}`,
  );
}

async function ensureCheckbox(page, labelPattern, logger) {
  const checkbox = page.getByLabel(labelPattern);
  if (await checkbox.count()) {
    const first = checkbox.first();
    if (await first.isVisible().catch(() => false)) {
      const checked = await first.isChecked().catch(() => false);
      if (!checked) {
        await first.check({ force: true });
        logger.write(`checked ${labelPattern}`);
      }
      return true;
    }
  }

  const textLocator = page.locator("label, span, div").filter({ hasText: labelPattern }).first();
  if (await textLocator.count()) {
    await textLocator.click({ force: true });
    logger.write(`clicked checkbox text ${labelPattern}`);
    return true;
  }

  logger.note(`WARNING: checkbox not found: ${labelPattern}`);
  return false;
}

async function setAdobeFileType(page, fileType, logger) {
  if (!fileType) return;

  const targetLabel = fileType === "illustrations" ? "Illustrations" : "Photos";
  const targetValue = fileType === "illustrations" ? "2" : "1";
  const fileTypeSelect = page.locator('select[name="contentType"]');
  if (!(await fileTypeSelect.count())) {
    throw new Error("Could not find Adobe File type select");
  }
  if (await fileTypeSelect.inputValue() === targetValue) {
    logger.write(`file type already set to ${targetLabel}`);
    return;
  }

  logger.write(`set Adobe file type to ${targetLabel}`);
  const fileTypeButton = page
    .getByRole("button")
    .filter({ hasText: /^(Photos|Illustrations)$/i })
    .first();

  if (!(await fileTypeButton.count())) {
    throw new Error("Could not find Adobe File type dropdown button");
  }

  await fileTypeButton.click();
  await page.getByRole("option", { name: new RegExp(`^${targetLabel}$`, "i") }).click();
  await page.waitForTimeout(1000);

  if (await fileTypeSelect.inputValue() !== targetValue) {
    throw new Error(`Could not verify Adobe File type as ${targetLabel}`);
  }
}

async function processUploadedRows(page, rows, configureRow) {
  const tiles = page.locator(".upload-tile__wrapper");
  if (await tiles.count() < rows.length) {
    throw new Error(`Adobe grid has fewer than ${rows.length} uploaded tiles`);
  }

  const verified = [];
  for (let index = 0; index < rows.length; index += 1) {
    await tiles.nth(index).click();
    await page.waitForTimeout(500);
    const bodyText = await page.locator("body").innerText({ timeout: 15000 });
    const originalName = /Original name\(s\):\s*([^\r\n]+)/i.exec(bodyText)?.[1]?.trim();
    const row = rows.find((candidate) => candidate.filename === originalName);
    if (!row) {
      throw new Error(`Newest Adobe tile does not match this batch: ${originalName || "unknown filename"}`);
    }
    if (verified.includes(row.filename)) {
      throw new Error(`Duplicate Adobe tile encountered for ${row.filename}`);
    }

    const title = await page.locator('textarea[name="title"]').inputValue();
    if (title.trim() !== row.title.trim()) {
      throw new Error(`Adobe title does not match CSV for ${row.filename}`);
    }
    const actualKeywords = (await page.locator('textarea[name="keywordsUITextArea"]').inputValue())
      .split(",").map((keyword) => keyword.trim().toLocaleLowerCase()).filter(Boolean);
    const expectedKeywords = row.keywords.split(",")
      .map((keyword) => keyword.trim().toLocaleLowerCase()).filter(Boolean);
    if (actualKeywords.join("\n") !== expectedKeywords.join("\n")) {
      throw new Error(`Adobe keywords do not match CSV for ${row.filename}`);
    }

    await configureRow(row);
    verified.push(row.filename);
  }

  if (verified.length !== rows.length) {
    throw new Error(`Verified ${verified.length} of ${rows.length} Adobe uploads`);
  }
  return verified;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    return;
  }

  const csvPath = path.resolve(options.csv);
  if (!fs.existsSync(csvPath)) throw new Error(`CSV not found: ${csvPath}`);
  const rows = parseAdobeCsv(csvPath);
  const imagePaths = resolveImages(options.images, path.dirname(csvPath), rows);
  const pendingImagePaths = resumeImagePaths(imagePaths, options.resumeAfter);
  if (options.dryRun) {
    await loadPlaywright();
    console.log(JSON.stringify({
      ok: true,
      mode: "dry-run",
      csv: csvPath,
      rows: rows.length,
      images: pendingImagePaths,
      browser: options.cdp ? { cdp: options.cdp } : { channel: options.channel, userDataDir: options.userDataDir || ".pw-edge-adobe-profile" },
    }, null, 2));
    return;
  }

  const runDir = nextRunDir(path.resolve(options.runRoot));
  fs.copyFileSync(fileURLToPath(import.meta.url), path.join(runDir, "final_script.py.txt"));
  const logger = makeLogger(runDir);
  fs.writeFileSync(
    path.join(runDir, "plan.md"),
    [
      "# Critical Points",
      "- [ ] CP1: Open Adobe Contributor uploads page.",
      "- [ ] CP2: Upload image files whose basenames match CSV Filename values.",
      "- [ ] CP3: Upload CSV metadata file.",
      "- [ ] CP4: Verify Adobe reports CSV data was applied or metadata is visible.",
      "- [ ] CP5: Do not click final Submit button.",
      "",
    ].join("\n"),
  );

  logger.write(`params: csv=${csvPath}; images=${imagePaths.join(", ")}; url=${options.url}`);

  const { chromium } = await loadPlaywright();
  let browser = null;
  let context = null;

  if (options.cdp) {
    logger.write(`connect to existing browser over CDP: ${options.cdp}`);
    browser = await chromium.connectOverCDP(options.cdp);
    context = browser.contexts()[0] || await browser.newContext();
  } else {
    const userDataDir = path.resolve(options.userDataDir || path.join(".pw-edge-adobe-profile"));
    logger.write(`launch persistent browser: channel=${options.channel}; userDataDir=${userDataDir}`);
    context = await chromium.launchPersistentContext(userDataDir, {
      channel: options.channel,
      headless: options.headless,
      viewport: { width: 1280, height: 1800 },
      acceptDownloads: true,
    });
  }

  context.setDefaultTimeout(options.timeout);
  const existingAdobePage = context.pages().find((candidate) => /contributor\.stock\.adobe\.com/i.test(candidate.url()));
  const page = existingAdobePage || await context.newPage();
  page.setDefaultTimeout(options.timeout);

  try {
    logger.write("open Adobe Contributor uploads page");
    await page.goto(options.url, { waitUntil: "domcontentloaded", timeout: options.timeout });
    await page.waitForLoadState("networkidle", { timeout: Math.min(options.timeout, 30000) }).catch(() => {});
    await screenshot(page, runDir, "01_open_uploads", logger);

    const bodyAfterOpen = await page.locator("body").innerText({ timeout: 15000 });
    if (/sign in|log in|continue with adobe|enter your email/i.test(bodyAfterOpen)) {
      throw new Error("Adobe login is required. Log in manually, then rerun with the same browser profile or CDP endpoint.");
    }
    const initialFileCountMatch = /File types:\s*All\s*\((\d+)\)/i.exec(bodyAfterOpen);
    if (!initialFileCountMatch) {
      throw new Error("Could not determine the initial Adobe uploaded-file count");
    }
    const initialFileCount = Number(initialFileCountMatch[1]);

    if (options.resumeAfter) {
      logger.write(`resume after ${options.resumeAfter} verified image(s); ${pendingImagePaths.length} remain`);
    }
    const imageChunks = options.cdp
      ? chunkFilesBySize(pendingImagePaths, 45_000_000)
      : pendingImagePaths.length ? [pendingImagePaths] : [];
    logger.write(
      `upload image files in ${imageChunks.length} batch(es): ${pendingImagePaths.map((file) => path.basename(file)).join(", ")}`,
    );
    let verifiedFileCount = initialFileCount;
    let textAfterImages = bodyAfterOpen;
    for (let index = 0; index < imageChunks.length; index += 1) {
      const chunk = imageChunks[index];
      logger.write(
        `upload image batch ${index + 1}/${imageChunks.length}: ${chunk.map((file) => path.basename(file)).join(", ")}`,
      );
      await clickFirstVisible(page.getByRole("button", { name: /^Upload$/ }), "top Upload button");
      await page.getByText(/Upload your files to start selling/i).waitFor({ state: "visible", timeout: 20000 });
      await screenshot(page, runDir, `02_upload_modal_${index + 1}`, logger);

      await setFilesFromChooser(
        page,
        async () => {
          const browse = page.getByRole("button", { name: /^Browse$/ });
          if (await browse.count()) await browse.first().click();
          else await page.getByText(/^Browse$/).click();
        },
        chunk,
        options.timeout,
      );

      logger.write(`wait for image batch ${index + 1}/${imageChunks.length} to finish`);
      textAfterImages = await waitForNoUploadRemaining(
        page, options.timeout, verifiedFileCount, chunk.length,
      );
      verifiedFileCount += chunk.length;
      await screenshot(page, runDir, `03_images_uploaded_${index + 1}`, logger);
      logger.write(`verified Adobe file count increased to ${verifiedFileCount}`);
    }
    if (/couldn't be uploaded|resolution is too small|failed/i.test(textAfterImages)) {
      logger.note("WARNING: Adobe page contains an upload error banner. Inspect screenshot and page state before relying on this run.");
    }

    logger.write(`upload CSV metadata: ${csvPath}`);
    await clickFirstVisible(page.getByRole("button", { name: /^Upload CSV$/ }), "Upload CSV button");
    await page.getByText(/Upload CSV file with your metadata/i).waitFor({ state: "visible", timeout: 20000 });
    await screenshot(page, runDir, "04_csv_modal", logger);

    await setFilesFromChooser(
      page,
      async () => {
        await page.getByRole("button", { name: /Choose CSV file and upload/i }).click();
      },
      csvPath,
      options.timeout,
    );

    logger.write("wait for CSV metadata application");
    const csvApplication = await waitForCsvApplication(page, rows, options.timeout, logger);
    logger.write(`CSV metadata wait finished: ${csvApplication.status}`);
    await screenshot(page, runDir, "05_csv_uploaded", logger);

    const refreshButton = page.getByRole("button", { name: /Refresh to view changes/i });
    if (await refreshButton.count()) {
      logger.write("refresh Adobe page to view CSV changes");
      await refreshButton.first().click();
      await page.waitForLoadState("networkidle", { timeout: Math.min(options.timeout, 30000) }).catch(() => {});
      await page.waitForTimeout(3000);
    }

    const verifiedFilenames = await processUploadedRows(page, rows, async (row) => {
      logger.write(`verified Adobe title and keywords for ${row.filename}`);
      await setAdobeFileType(page, options.fileType, logger);

      if (options.markAi) {
        await ensureCheckbox(page, /Created using generative AI tools/i, logger);
      }

      if (options.markFictional) {
        await ensureCheckbox(page, /People and Property are fictional/i, logger);
      }

      if (options.saveWork) {
        const save = page.getByRole("button", { name: /^Save work$/ });
        if (await save.count()) {
          const first = save.first();
          if (await first.isEnabled().catch(() => false)) {
            logger.write(`save Adobe draft work for ${row.filename}`);
            await first.click();
            await page.waitForTimeout(3000);
          } else {
            logger.note(`Save work button is present but disabled for ${row.filename}.`);
          }
        } else {
          logger.note(`Save work button was not found for ${row.filename}.`);
        }
      }
    });
    logger.write(`verified and configured uploaded files: ${verifiedFilenames.join(", ")}`);

    const finalText = await page.locator("body").innerText({ timeout: 15000 });
    if (/Submit\s+\d+\s+file/i.test(finalText)) {
      logger.note("Final submit button is visible. This script intentionally did not click it.");
    }

    const finalScreenshot = await screenshot(page, runDir, "06_final_verified_no_submit", logger);
    logger.note(`FINAL_STATUS: prepared Adobe upload metadata without final submission`);
    if (finalScreenshot) logger.note(`FINAL_SCREENSHOT: ${finalScreenshot}`);
    logger.note(`RUN_DIR: ${runDir}`);
  } finally {
    if (!options.cdp) {
      await context?.close().catch(() => {});
    }
  }
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error.stack || error.message || String(error));
    process.exit(1);
  });

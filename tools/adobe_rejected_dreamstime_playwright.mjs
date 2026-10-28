#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const modulePath = fileURLToPath(import.meta.url);
const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(modulePath);

export function parseArgs(argv) {
  const out = { cdp: "http://127.0.0.1:9222", days: 14, submitCommercial: false, timeout: 120000 };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--submit-commercial") out.submitCommercial = true;
    else if (["--cdp", "--scan-output", "--manifest", "--run-dir", "--days", "--timeout", "--since"].includes(key)) {
      const value = argv[++i];
      if (!value) throw new Error(`${key} requires a value`);
      const name = key.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      out[name] = ["days", "timeout"].includes(name) ? Number(value) : value;
    } else throw new Error(`unknown argument: ${key}`);
  }
  if (!out.scanOutput && !out.manifest) throw new Error("provide --scan-output and/or --manifest");
  if (!/^https?:\/\//.test(out.cdp) || !Number.isFinite(out.days) || out.days < 0) throw new Error("invalid CDP URL or days");
  if (out.since && Number.isNaN(new Date(out.since).valueOf())) throw new Error("invalid --since date");
  return out;
}

export function parseSubmittedAt(text, now = new Date()) {
  const match = /(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago/i.exec(text);
  if (!match) {
    const parsed = new Date(text.replace(/^Submitted\s*/i, ""));
    if (Number.isNaN(parsed.valueOf())) throw new Error(`unsupported Adobe submitted date: ${text}`);
    return parsed;
  }
  const amount = Number(match[1]);
  const days = { minute: amount / 1440, hour: amount / 24, day: amount, week: amount * 7, month: amount * 30, year: amount * 365 }[match[2].toLowerCase()];
  return new Date(now.valueOf() - days * 86400000);
}

export function isEligible(submittedAt, since) { return submittedAt >= since; }

export function chunkFilesBySize(files, limit = 45_000_000, sizeOf = (file) => fs.statSync(file).size) {
  const chunks = [];
  let current = [], size = 0;
  for (const file of files) {
    const fileSize = sizeOf(file);
    if (fileSize > limit) throw new Error(`file exceeds CDP safe size: ${file}`);
    if (current.length && size + fileSize > limit) { chunks.push(current); current = []; size = 0; }
    current.push(file); size += fileSize;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

async function loadPlaywright() {
  try { return await import("playwright"); } catch (first) {
    const require = createRequire(import.meta.url);
    const roots = [process.env.PLAYWRIGHT_NODE_MODULES, path.resolve("node_modules"), process.env.APPDATA && path.join(process.env.APPDATA, "npm", "node_modules")].filter(Boolean);
    for (const root of roots) {
      try { return require(path.join(root, "playwright")); } catch {}
    }
    throw new Error(`Could not load Playwright: ${first.message}`);
  }
}

async function screenshot(page, runDir, name) {
  fs.mkdirSync(runDir, { recursive: true });
  const target = path.join(runDir, `${name}.png`);
  await page.screenshot({ path: target, fullPage: true }).catch(() => {});
  return target;
}

async function scanAdobe(page, options, runDir) {
  await page.goto("https://contributor.stock.adobe.com/en/uploads/rejected", { waitUntil: "domcontentloaded", timeout: options.timeout });
  await page.waitForTimeout(3000);
  if (/sign in|log in|continue with adobe/i.test(await page.locator("body").innerText())) throw new Error("Adobe login required");
  const since = options.since ? new Date(options.since) : new Date(Date.now() - options.days * 86400000);
  const records = new Map();
  const pageSignatures = new Set();
  let pageNumber = 1;
  while (pageNumber <= 100) {
    const images = page.locator('img[alt="thumbnail"]');
    const count = await images.count();
    console.log(`[adobe] scan page=${pageNumber} thumbnails=${count}`);
    if (!count) throw new Error("Adobe rejected thumbnails were not found");
    const signature = await images.first().getAttribute("src") || `page-${pageNumber}`;
    if (pageSignatures.has(signature)) break;
    pageSignatures.add(signature);
    for (let index = 0; index < count; index += 1) {
      const image = images.nth(index);
      const source = await image.getAttribute("src") || "";
      const expectedFileId = /220_F_(\d+)_/.exec(source)?.[1];
      await image.evaluate((node) => (node.parentElement || node).click());
      if (expectedFileId) {
        await page.waitForFunction(
          (fileId) => document.querySelector(".content-tagger__form--content")?.innerText.includes(`File ID: ${fileId}`),
          expectedFileId,
          { timeout: 3000 },
        ).catch(() => {});
      }
      const panel = page.locator(".content-tagger__form--content").first();
      const detail = await panel.innerText().catch(() => "");
      const original = /Original name:\s*([^\s]+)/i.exec(detail)?.[1];
      const submittedText = /Submitted\s+([^\n]+)/i.exec(detail)?.[0];
      if (!original || !submittedText) continue;
      const submittedAt = parseSubmittedAt(submittedText);
      if (!isEligible(submittedAt, since)) continue;
      const title = detail.split(/Submitted\s+/i)[0].trim().replace(/\s+/g, " ");
      const reason = /MISSING GENERATIVE AI FLAG|QUALITY ISSUES|SIMILAR CONTENT|INTELLECTUAL PROPERTY|NON COMPLIANT IMAGE/i.exec(detail)?.[0] || "NOT_ACCEPTED";
      records.set(original.toLowerCase(), { title, original_name: original, submitted_at: submittedAt.toISOString(), reason });
      if ((index + 1) % 10 === 0 || index + 1 === count) console.log(`[adobe] page=${pageNumber} inspected=${index + 1}/${count} eligible=${records.size}`);
    }
    const next = page.getByText(/^Next$/i).last();
    if (!await next.count() || !await next.isEnabled().catch(() => false)) break;
    await next.click();
    await page.waitForTimeout(1500);
    pageNumber += 1;
  }
  const values = [...records.values()].sort((a, b) => b.submitted_at.localeCompare(a.submitted_at));
  fs.mkdirSync(path.dirname(path.resolve(options.scanOutput)), { recursive: true });
  fs.writeFileSync(options.scanOutput, JSON.stringify(values, null, 2) + "\n");
  await screenshot(page, runDir, "adobe_rejected_scan_complete");
  console.log(`[adobe] eligible rejected files=${values.length} output=${options.scanOutput}`);
}

async function requireLocator(locator, label) {
  if (!await locator.count()) throw new Error(`Dreamstime field not found: ${label}`);
  const target = locator.first();
  await target.waitFor({ state: "visible", timeout: 15000 });
  return target;
}

async function findField(page, patterns, label) {
  for (const pattern of patterns) {
    const field = page.getByLabel(pattern).or(page.getByPlaceholder(pattern));
    if (await field.count()) return field.first();
  }
  throw new Error(`Dreamstime field not found: ${label}`);
}

async function fillRequired(page, patterns, value, label) {
  const field = await findField(page, patterns, label);
  await field.fill(String(value));
  if ((await field.inputValue()).trim() !== String(value).trim()) throw new Error(`Dreamstime field did not retain value: ${label}`);
}

async function setCheckedRequired(page, pattern, checked, label) {
  const target = await requireLocator(page.getByLabel(pattern), label);
  if (await target.isChecked() !== checked) await target.click();
  if (await target.isChecked() !== checked) throw new Error(`Dreamstime checkbox state is wrong: ${label}`);
}

async function selectRequired(page, pattern, label) {
  const target = await requireLocator(page.getByText(pattern).or(page.getByRole("button", { name: pattern })), label);
  await target.click();
}

function saveManifest(manifestPath, manifest) {
  const temporary = `${manifestPath}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(manifest, null, 2) + "\n");
  fs.renameSync(temporary, manifestPath);
}

async function prepareDreamstime(page, options, runDir) {
  const manifest = JSON.parse(fs.readFileSync(options.manifest, "utf8"));
  const items = manifest.items || [];
  await page.goto("https://www.dreamstime.com/upload", { waitUntil: "domcontentloaded", timeout: options.timeout });
  await page.waitForTimeout(3000);
  if (/sign in|log in/i.test(await page.locator("body").innerText())) throw new Error("Dreamstime login required");
  const pending = items.filter((item) => !["uploaded", "prepared", "submitted"].includes(item.dreamstime_status));
  for (const [batchIndex, batch] of chunkFilesBySize(pending.map((item) => item.image_path)).entries()) {
    const upload = page.getByRole("button", { name: /upload|browse|select files/i }).first();
    if (await upload.count()) {
      const [chooser] = await Promise.all([
        page.waitForEvent("filechooser", { timeout: options.timeout }),
        upload.click(),
      ]);
      await chooser.setFiles(batch);
    } else {
      await page.locator("input[type=file]").first().setInputFiles(batch);
    }
    console.log(`[dreamstime] uploaded batch ${batchIndex + 1}`);
    for (const file of batch) {
      const name = path.basename(file);
      await page.getByText(name, { exact: false }).first().waitFor({ state: "visible", timeout: options.timeout });
      const item = pending.find((candidate) => path.resolve(candidate.image_path) === path.resolve(file));
      if (item) item.dreamstime_status = "uploaded";
    }
    saveManifest(options.manifest, manifest);
  }
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const fileName = path.basename(item.image_path);
    if (item.dreamstime_status === "submitted" || (item.dreamstime_status === "prepared" && !options.submitCommercial)) continue;
    const fileLink = await requireLocator(page.getByText(fileName, { exact: false }), `uploaded file ${fileName}`);
    await fileLink.click();
    await page.waitForTimeout(1500);
    if (!(await page.locator("body").innerText()).includes(fileName)) throw new Error(`Dreamstime did not select ${fileName}`);
    await fillRequired(page, [/image name|title/i], item.title, "image title");
    await fillRequired(page, [/description/i], item.spreadsheet_row.Description, "description");
    const keywordHeading = page.getByText(/^Keywords\*?$/i).first();
    await requireLocator(keywordHeading, "Keywords heading");
    const headingRow = keywordHeading.locator("xpath=..");
    const clear = headingRow.locator('button,[role="button"],[aria-label*="clear" i],[title*="clear" i]').first();
    await (await requireLocator(clear, "clear all keywords")).click();
    const keywordInput = await findField(page, [/add keyword/i], "keyword input");
    for (const keyword of item.keywords) {
      await keywordInput.fill(keyword);
      await keywordInput.press("Enter");
      if (!(await keywordHeading.locator("xpath=../..").innerText()).toLowerCase().includes(keyword.toLowerCase())) {
        throw new Error(`Dreamstime keyword was not added: ${keyword}`);
      }
    }
    await setCheckedRequired(page, /Generative AI category/i, true, "AI Contributor");
    await setCheckedRequired(page, /Web Usage.*W-EL/i, true, "Web Usage");
    await setCheckedRequired(page, /Print Usage.*P-EL/i, true, "Print Usage");
    await setCheckedRequired(page, /Sell the rights.*SR-EL/i, true, "Sell the rights");
    await setCheckedRequired(page, /free section.*RF-LL/i, false, "RF-LL");
    await setCheckedRequired(page, /exclusively on Dreamstime/i, false, "Dreamstime exclusive");
    await selectRequired(page, /Commercial\s*\(RF\)/i, "Commercial license");
    await selectRequired(page, /Custom price/i, "Custom price option");
    await fillRequired(page, [/custom price/i], "2", "custom price");
    await screenshot(page, runDir, `dreamstime_${index + 1}_${item.asset_id}_prepared`);
    item.dreamstime_status = "prepared";
    item.dreamstime_updated_at = new Date().toISOString();
    saveManifest(options.manifest, manifest);
    if (options.submitCommercial) {
      await (await requireLocator(page.getByRole("button", { name: /Submit commercial/i }), "Submit commercial")).click();
      await page.waitForTimeout(2000);
      item.dreamstime_status = "submitted";
      item.dreamstime_updated_at = new Date().toISOString();
      saveManifest(options.manifest, manifest);
      console.log(`[dreamstime] submitted ${item.asset_id}`);
    } else console.log(`[dreamstime] prepared without final submit ${item.asset_id}`);
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const runDir = path.resolve(options.runDir || path.join("output", "dreamstime_rejections", new Date().toISOString().replace(/[:.]/g, "-")));
  const { chromium } = await loadPlaywright();
  const browser = await chromium.connectOverCDP(options.cdp);
  const context = browser.contexts()[0];
  const page = context.pages()[0] || await context.newPage();
  if (options.scanOutput) await scanAdobe(page, options, runDir);
  if (options.manifest) await prepareDreamstime(page, options, runDir);
  console.log(`[run] artifacts=${runDir}`);
}

if (isMain) {
  main()
    .then(() => process.exit(0))
    .catch((error) => { console.error(error.stack || error); process.exit(1); });
}

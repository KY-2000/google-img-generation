import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadPlaywright } from "./adobe_rejected_dreamstime_playwright.mjs";

const HISTORY = "https://www.dreamstime.com/upload/uploads-history";
const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

export async function openHistory(page, existing, refresh = false) {
  const table = page.getByRole("table").filter({ hasText: "ORIGINAL FILENAME" });
  const navigation = { waitUntil: "domcontentloaded", timeout: 15_000 };
  if (!existing) {
    await page.goto(HISTORY, navigation);
  } else if (refresh && await table.isVisible()) {
    await page.reload(navigation);
  }
  try {
    await table.waitFor({ timeout: 12_000 });
  } catch {
    await page.bringToFront().catch(() => {});
    const text = await page.locator("body").innerText({ timeout: 2000 }).catch(() => "");
    if (/confirm you are|not a bot|press & hold/i.test(text)) {
      throw new Error("Dreamstime needs human verification. Complete it in the opened Edge tab, then click Retry Failed.");
    }
    throw new Error("Dreamstime Upload History did not show its filename table. Check the opened Edge tab, then click Retry Failed.");
  }
  return table;
}

async function readRows(table) {
  return table.getByRole("row").evaluateAll(rows => rows.flatMap(row => {
    const cells = Array.from(row.querySelectorAll("td"), cell => cell.innerText.trim());
    return cells.length === 4 ? [[cells[2], cells[3]]] : [];
  }));
}

async function readPage(table, jump, index, currentRows) {
  const previous = JSON.stringify(currentRows);
  await jump.fill(String(index), { timeout: 3000 });
  await jump.press("Enter", { timeout: 3000 });
  for (let retry = 0; retry < 20; retry++) {
    const rows = await readRows(table);
    if (JSON.stringify(rows) !== previous) return rows;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error("Could not verify all pages of Dreamstime Upload History.");
}

async function main(cdp, refresh) {
  const { chromium } = await loadPlaywright();
  const browser = await chromium.connectOverCDP(cdp, { timeout: 10_000 });
  const context = browser.contexts()[0];
  const existing = context.pages().find(page => page.url() === HISTORY);
  const page = existing || await context.newPage();
  try {
    const table = await openHistory(page, Boolean(existing), refresh);
    const items = new Map();
    const indicator = page.getByText(/^of [\d,]+ pages$/i);
    const count = await indicator.count() ? Number((await indicator.innerText()).replace(/\D/g, "")) : 1;
    const jump = page.getByRole("list").filter({ has: indicator }).last().getByRole("textbox");
    let rows = await readRows(table);
    if (count > 1 && (await jump.inputValue({ timeout: 3000 })) !== "1") {
      rows = await readPage(table, jump, 1, rows);
    }
    for (let index = 1; index <= count; index++) {
      if (index > 1) rows = await readPage(table, jump, index, rows);
      for (const [name, status] of rows) {
        if (!items.has(name)) items.set(name, status);
      }
    }
    console.log(JSON.stringify(Object.fromEntries(items)));
  } finally {
    await browser.close();
  }
}

if (isMain) {
  main(process.argv[2], process.argv.includes("--refresh")).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}

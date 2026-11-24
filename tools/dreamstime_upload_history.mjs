import { loadPlaywright } from "./adobe_rejected_dreamstime_playwright.mjs";

const HISTORY = "https://www.dreamstime.com/upload/uploads-history";

async function readRows(table) {
  return table.getByRole("row").evaluateAll(rows => rows.flatMap(row => {
    const cells = Array.from(row.querySelectorAll("td"), cell => cell.innerText.trim());
    return cells.length === 4 ? [[cells[2], cells[3]]] : [];
  }));
}

async function main(cdp) {
  const { chromium } = await loadPlaywright();
  const browser = await chromium.connectOverCDP(cdp, { timeout: 10_000 });
  const context = browser.contexts()[0];
  const existing = context.pages().find(page => page.url() === HISTORY);
  const page = existing || await context.newPage();
  let signedIn = false;
  try {
    await page.goto(HISTORY, { waitUntil: "domcontentloaded", timeout: 15_000 });
    await page.getByRole("link", { name: "Back to uploads" }).waitFor({ timeout: 12_000 }).catch(() => {
      throw new Error("Open Dreamstime in the automation Edge window, sign in or complete its verification, then retry.");
    });
    signedIn = true;
    const table = page.getByRole("table").filter({ hasText: "ORIGINAL FILENAME" });
    await table.waitFor({ timeout: 5000 }).catch(() => {
      throw new Error("Dreamstime Upload History did not expose its filename table.");
    });
    const items = new Map();
    const indicator = page.getByText(/^of [\d,]+ pages$/i);
    const count = await indicator.count() ? Number((await indicator.innerText()).replace(/\D/g, "")) : 1;
    const paginator = page.getByRole("list").filter({ has: indicator }).last();
    let rows = await readRows(table);
    for (let index = 1; index <= count; index++) {
      if (index > 1) {
        const signature = JSON.stringify(rows);
        const jump = paginator.getByRole("textbox");
        await jump.fill(String(index), { timeout: 3000 });
        await jump.press("Enter", { timeout: 3000 });
        for (let retry = 0; retry < 20 && JSON.stringify(rows) === signature; retry++) {
          await new Promise(resolve => setTimeout(resolve, 200));
          rows = await readRows(table);
        }
        if (JSON.stringify(rows) === signature) throw new Error("Could not verify all pages of Dreamstime Upload History.");
      }
      for (const [name, status] of rows) {
        if (!items.has(name)) items.set(name, status);
      }
    }
    console.log(JSON.stringify(Object.fromEntries(items)));
  } finally {
    if (!existing && signedIn) await page.close().catch(() => {});
    await browser.close();
  }
}

main(process.argv[2]).catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});

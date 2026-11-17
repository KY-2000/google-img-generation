import fs from "node:fs";
import path from "node:path";

const ORIGINAL = /Original name\(s\):\s*([^\r\n]+)/i;
const TOTAL = /File types:\s*All\s*\((\d+)\)/i;
const AI_LABEL = /Created using generative AI tools/i;

export function createAdobeDraftAdapter({
  page, options, runDir, logger, clickFirstVisible, setFilesFromChooser,
  waitForNoUploadRemaining, waitForCsvApplication, setAdobeFileType, screenshot,
}) {
  const tiles = () => page.locator(".upload-tile__wrapper");
  const body = () => page.locator("body").innerText({ timeout: 15000 });
  const open = async index => {
    await tiles().nth(index).click();
    await page.waitForTimeout(500);
    return ORIGINAL.exec(await body())?.[1]?.trim();
  };
  const count = async () => {
    const current = new URL(page.url());
    if (current.origin !== "https://contributor.stock.adobe.com" || !/^\/en\/uploads\/?$/.test(current.pathname)) {
      throw new Error("Adobe login or Contributor uploads page is unavailable");
    }
    const match = TOTAL.exec(await body());
    if (!match) throw new Error("Cannot confirm Adobe All uploads count; check login and page layout");
    return Number(match[1]);
  };
  const inventory = async () => {
    const expected = await count();
    if (await tiles().count() !== expected) {
      throw new Error("Adobe upload grid does not expose every draft; reset filters or check for pagination before retry");
    }
    const files = new Map();
    for (let index = 0; index < expected; index += 1) {
      const filename = await open(index);
      if (!filename || files.has(filename)) {
        throw new Error("Adobe draft inventory contains an ambiguous or duplicate original filename");
      }
      files.set(filename, index);
    }
    if (await count() !== expected) throw new Error("Adobe drafts changed during inventory");
    return files;
  };
  let indices = new Map();
  const select = async row => {
    const index = indices.get(row.filename);
    if (index !== undefined && await open(index) === row.filename) return;
    indices = await inventory();
    if (!indices.has(row.filename) || await open(indices.get(row.filename)) !== row.filename) {
      throw new Error("Adobe draft not found by exact filename: " + row.filename);
    }
  };
  const keywords = value => value.split(",").map(s => s.trim().toLowerCase()).filter(Boolean).join("\n");
  const ai = () => page.getByRole("checkbox", { name: AI_LABEL });
  const verifyMetadata = async row => {
    const title = await page.locator('textarea[name="title"]').inputValue();
    const actualKeywords = await page.locator('textarea[name="keywordsUITextArea"]').inputValue();
    const category = page.locator('select[name="category"], select[name="categoryId"], select[name="contentCategory"]');
    if (await category.count() !== 1) throw new Error("Cannot verify Adobe category selector");
    if (title.trim() !== row.title.trim() || keywords(actualKeywords) !== keywords(row.keywords) ||
        await category.inputValue() !== row.category) {
      throw new Error("Adobe title, keywords or category differ from CSV for " + row.filename);
    }
  };
  const reload = async () => {
    await page.reload({ waitUntil: "domcontentloaded", timeout: options.timeout });
    await page.waitForLoadState("networkidle", { timeout: Math.min(options.timeout, 15000) }).catch(() => {});
    await page.getByText(/File types:/i).first().waitFor({ state: "visible", timeout: options.timeout });
  };
  const verifySaved = async row => {
    await reload();
    await select(row);
    await verifyMetadata(row);
    if (await page.locator('select[name="contentType"]').inputValue() !== "2" ||
        await ai().count() !== 1 || !await ai().isChecked()) {
      throw new Error("Adobe draft did not retain illustrations and Generative AI settings for " + row.filename);
    }
    const save = page.getByRole("button", { name: /^Save work$/ });
    if (await save.count() && await save.first().isEnabled()) {
      throw new Error("Adobe still shows unsaved changes for " + row.filename);
    }
  };
  return {
    inventory,
    shouldStop: () => Boolean(options.managedStopFile && fs.existsSync(options.managedStopFile)),
    async upload(file, recordAttempt) {
      const before = await count();
      await clickFirstVisible(page.getByRole("button", { name: /^Upload$/ }), "top Upload button");
      await page.getByText(/Upload your files to start selling/i).waitFor({ state: "visible", timeout: 20000 });
      await setFilesFromChooser(page, async () => {
        const browse = page.getByRole("button", { name: /^Browse$/ });
        if (await browse.count()) await browse.first().click();
        else await page.getByText(/^Browse$/).click();
        // Persist before Playwright receives permission to select the file.
        recordAttempt();
      }, [file], options.timeout);
      await waitForNoUploadRemaining(page, options.timeout, before, 1);
      logger.write("Adobe received " + path.basename(file));
    },
    async importCsv(rows) {
      const quote = cell => '"' + String(cell).replace(/"/g, '""') + '"';
      const csvPath = path.join(runDir, "managed_metadata.csv");
      fs.writeFileSync(csvPath, ["Filename,Title,Keywords,Category,Releases", ...rows.map(row =>
        [row.filename, row.title, row.keywords, row.category, row.releases].map(quote).join(","))].join("\n") + "\n", "utf8");
      await clickFirstVisible(page.getByRole("button", { name: /^Upload CSV$/ }), "Upload CSV button");
      await page.getByText(/Upload CSV file with your metadata/i).waitFor({ state: "visible", timeout: 20000 });
      await setFilesFromChooser(page, () => page.getByRole("button", { name: /Choose CSV file and upload/i }).click(),
        csvPath, options.timeout);
      await waitForCsvApplication(page, rows, options.timeout, logger);
      const refresh = page.getByRole("button", { name: /Refresh to view changes/i });
      if (await refresh.count()) await refresh.first().click();
      await reload();
      indices = await inventory();
    },
    verifySaved,
    async saveAndVerify(row) {
      await select(row);
      await verifyMetadata(row);
      await setAdobeFileType(page, "illustrations", logger);
      if (await ai().count() !== 1) throw new Error("Cannot uniquely verify the Generative AI checkbox");
      if (!await ai().isChecked()) await ai().check();
      const save = page.getByRole("button", { name: /^Save work$/ });
      if (await save.count() === 1 && await save.isEnabled()) {
        await save.click();
        // Reopening and checking saved fields is required even if the click returned OK.
        await page.waitForTimeout(1500);
      }
      await verifySaved(row);
      await screenshot(page, runDir, "saved_" + row.filename, logger);
    },
    confirmed(filename) { logger.write("Verified saved Adobe draft: " + filename); },
  };
}

import fs from "node:fs";
import path from "node:path";

const TOTAL = /File types:\s*All\s*\((\d+)\)/i;
const AI_LABEL = /Created using generative AI tools/i;
const CATEGORY_NAMES = new Map(
  Array.from(
    fs.readFileSync(new URL("../category-code.txt", import.meta.url), "utf8").matchAll(/^(\d+)\.\s+(.+)$/gm),
    ([, code, name]) => [code, name.trim()],
  ),
);

export function createAdobeDraftAdapter({
  page, options, runDir, logger, clickFirstVisible, setFilesFromChooser,
  waitForNoUploadRemaining, waitForCsvApplication, setAdobeFileType, screenshot,
  imageUploadTimeout = () => options.timeout,
}) {
  const tiles = () => page.locator(".upload-tile__wrapper");
  const body = () => page.locator("body").innerText({ timeout: 15000 });
  const paginator = () => page.locator(".the-paginator-list");
  const shouldStop = () => Boolean(options.managedStopFile && fs.existsSync(options.managedStopFile));
  const goToPage = async number => {
    const pagination = paginator();
    if (number === 1 && !await pagination.count()) return;
    const active = pagination.locator("a.blue").getByText(String(number), { exact: true });
    if (await active.count()) return;
    const input = pagination.locator(".paginator-goto-input");
    await input.fill(String(number), { timeout: options.timeout });
    await input.press("Enter", { timeout: options.timeout });
    await active.waitFor({ state: "visible", timeout: options.timeout });
  };
  const open = async index => {
    await tiles().nth(index).click();
    const details = await page.waitForFunction(position => {
      const tile = document.querySelectorAll(".upload-tile__wrapper")[position];
      if (!tile?.closest('[aria-selected="true"]')) return false;
      const thumbnail = tile.querySelector("img")?.getAttribute("src");
      const panel = '[data-t="content-side-panel"]';
      const preview = document.querySelector(`${panel} [data-t="asset-sidebar-header-thumbnail"]`);
      if (!thumbnail || preview?.getAttribute("src") !== thumbnail) return false;
      const footer = document.querySelector(`${panel} [data-t="asset-sidebar-footer-file-ids"]`)?.innerText;
      const match = /File ID\(s\):\s*([\s\S]*?)Original name\(s\):\s*([^\r\n]+)/i.exec(footer || "");
      const fileIds = match?.[1].match(/\d+/g);
      const thumbnailIds = thumbnail.match(/\d+/g);
      // The header image can update before the filename. Require the footer's
      // own file ID to agree with the selected thumbnail before reading it.
      if (!fileIds?.some(id => thumbnailIds?.includes(id))) return false;
      return match[2].trim() || false;
    }, index, { timeout: Math.min(options.timeout, 10000) }).catch(error => {
      throw new Error(`Cannot confirm Adobe details for draft tile ${index + 1}`, { cause: error });
    });
    try {
      return await details.jsonValue();
    } finally {
      await details.dispose();
    }
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
  const readInventory = async (wanted, showProgress) => {
    const expected = await count();
    const files = new Map();
    let scanned = 0;
    for (let pageNumber = 1; ; pageNumber += 1) {
      await goToPage(pageNumber);
      const visible = await tiles().count();
      if (scanned + visible > expected || (!visible && expected)) break;
      if (expected && showProgress) {
        logger.progress?.("checking", `Page ${pageNumber} · ${scanned} of ${expected} existing drafts checked`);
      }
      if (expected) logger.write(`Checking Adobe drafts page ${pageNumber} (${scanned + visible}/${expected})`);
      for (let index = 0; index < visible; index += 1) {
        if (shouldStop()) throw new Error("Stopped while checking Adobe drafts");
        const filename = await open(index);
        if (!filename) throw new Error("Cannot read an original filename from the Adobe draft grid");
        if (wanted && !wanted.has(filename)) continue;
        if (files.has(filename)) throw new Error("Adobe draft inventory contains a duplicate original filename: " + filename);
        files.set(filename, { pageNumber, index });
      }
      scanned += visible;
      if (!await paginator().getByText("Next", { exact: true }).count()) break;
      if (!visible || scanned >= expected) {
        throw new Error("Adobe grid has another page after reaching the reported draft count");
      }
    }
    if (scanned !== expected) {
      throw new Error(`Adobe upload inventory read ${scanned} of ${expected} drafts; check filters and pagination`);
    }
    if (await count() !== expected) throw new Error("Adobe drafts changed during inventory");
    await goToPage(1);
    return files;
  };
  const inventory = async (wanted, showProgress = false) => {
    for (let attempt = 0; ; attempt += 1) {
      const before = await count();
      try {
        return await readInventory(wanted, showProgress);
      } catch (error) {
        if (shouldStop() || attempt >= 2 || await count() === before) throw error;
        logger.write("Adobe draft count changed during inventory; rescanning the current grid");
      }
    }
  };
  let indices = new Map();
  const openLocation = async location => {
    await goToPage(location.pageNumber);
    if (location.index < await tiles().count()) return await open(location.index);
  };
  const select = async row => {
    const location = indices.get(row.filename);
    if (location && await openLocation(location) === row.filename) return;
    const found = (await inventory(new Set([row.filename]))).get(row.filename);
    if (!found || await openLocation(found) !== row.filename) {
      throw new Error("Adobe draft not found by exact filename: " + row.filename);
    }
    indices.set(row.filename, found);
  };
  const keywords = value => value.split(",").map(s => s.trim().toLowerCase()).filter(Boolean).join("\n");
  const ai = () => page.getByRole("checkbox", { name: AI_LABEL });
  const verifyMetadata = async row => {
    const title = await page.locator('textarea[name="title"]').inputValue();
    if (title.trim() !== row.title.trim()) {
      throw new Error("Adobe title differs from CSV for " + row.filename);
    }
    const actualKeywords = await page.locator('textarea[name="keywordsUITextArea"]').inputValue();
    if (keywords(actualKeywords) !== keywords(row.keywords)) {
      throw new Error("Adobe keywords differ from CSV for " + row.filename);
    }
    const category = page.locator('select[name="category"], select[name="categoryId"], select[name="contentCategory"]');
    if (await category.count() !== 1) throw new Error("Cannot verify Adobe category selector");
    const expected = CATEGORY_NAMES.get(String(row.category));
    if (!expected) throw new Error("Unknown Adobe CSV category code for " + row.filename);
    // The CSV code and the website's taxonomy option ID use different numbering.
    const actual = await category.evaluate(select => select.selectedOptions[0]?.label?.trim() || "");
    if (actual.toLowerCase() !== expected.toLowerCase()) {
      throw new Error(`Adobe category differs from CSV for ${row.filename}: expected ${expected}, found ${actual || "(empty)"}`);
    }
  };
  const reload = async ({ afterCsv = false } = {}) => {
    await page.reload({ waitUntil: "domcontentloaded", timeout: options.timeout });
    if (afterCsv) {
      await page.waitForLoadState("networkidle", { timeout: Math.min(options.timeout, 15000) }).catch(() => {});
    }
    await page.getByText(/File types:/i).first().waitFor({ state: "visible", timeout: options.timeout });
    await tiles().first().waitFor({ state: "visible", timeout: options.timeout });
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
    progress: logger.progress,
    async inventory(wanted) {
      logger.progress?.("checking");
      indices = await inventory(wanted, true);
      return indices;
    },
    shouldStop,
    async upload(files, recordAttempts) {
      logger.progress?.("uploading", `${files.length} file(s) in this batch`);
      const before = await count();
      logger.write(`Uploading image batch (${files.length} files)`);
      await clickFirstVisible(page.getByRole("button", { name: /^Upload$/ }), "top Upload button");
      await page.getByText(/Upload your files to start selling/i).waitFor({ state: "visible", timeout: 20000 });
      await setFilesFromChooser(page, async () => {
        const browse = page.getByRole("button", { name: /^Browse$/ });
        if (await browse.count()) await browse.first().click();
        else await page.getByText(/^Browse$/).click();
        // Record every file before the chooser dispatches this batch.
        recordAttempts();
      }, files, options.timeout);
      await waitForNoUploadRemaining(page, imageUploadTimeout(files), before, files.length);
      logger.write(`Adobe received image batch (${files.length} files): ${files.map(file => path.basename(file)).join(", ")}`);
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
      await reload({ afterCsv: true });
      indices = await inventory(new Set(rows.map(row => row.filename)));
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

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


function loadWaitForNoUploadRemaining() {
  const testDir = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(testDir, "../tools/adobe_upload_playwright.mjs"),
    "utf8",
  );
  const start = source.indexOf("async function waitForNoUploadRemaining");
  const end = source.indexOf("async function waitForCsvApplication", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return new Function(`${source.slice(start, end)}; return waitForNoUploadRemaining;`)();
}


function loadProcessUploadedRows() {
  const testDir = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(testDir, "../tools/adobe_upload_playwright.mjs"),
    "utf8",
  );
  const start = source.indexOf("async function processUploadedRows");
  const end = source.indexOf("async function main", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return new Function(`${source.slice(start, end)}; return processUploadedRows;`)();
}


function loadSetAdobeFileType() {
  const testDir = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(testDir, "../tools/adobe_upload_playwright.mjs"),
    "utf8",
  );
  const start = source.indexOf("async function setAdobeFileType");
  const end = source.indexOf("async function main", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return new Function(`${source.slice(start, end)}; return setAdobeFileType;`)();
}


function loadChunkFilesBySize() {
  const testDir = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(testDir, "../tools/adobe_upload_playwright.mjs"),
    "utf8",
  );
  const start = source.indexOf("function chunkFilesBySize");
  const end = source.indexOf("function nextRunDir", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return new Function(`${source.slice(start, end)}; return chunkFilesBySize;`)();
}


function loadScreenshot() {
  const testDir = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(testDir, "../tools/adobe_upload_playwright.mjs"),
    "utf8",
  );
  const start = source.indexOf("async function screenshot");
  const end = source.indexOf("async function clickFirstVisible", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return new Function("path", `${source.slice(start, end)}; return screenshot;`)(path);
}


function loadResumeImagePaths() {
  const testDir = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(testDir, "../tools/adobe_upload_playwright.mjs"),
    "utf8",
  );
  const start = source.indexOf("function resumeImagePaths");
  const end = source.indexOf("function chunkFilesBySize", start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return new Function(`${source.slice(start, end)}; return resumeImagePaths;`)();
}


test("waits for plural upload queue text and confirmed file-count increase", async () => {
  const waitForNoUploadRemaining = loadWaitForNoUploadRemaining();
  const pageTexts = [
    "File types: All (229)\n3 Uploads Remaining...",
    "File types: All (229)",
    "File types: All (232)",
  ];
  let reads = 0;
  const page = {
    locator() {
      return {
        async innerText() {
          const text = pageTexts[Math.min(reads, pageTexts.length - 1)];
          reads += 1;
          return text;
        },
      };
    },
    async waitForTimeout() {},
  };

  const result = await waitForNoUploadRemaining(page, 1000, 229, 3);

  assert.equal(reads, 3);
  assert.match(result, /All \(232\)/);
});


test("verifies each new tile by original filename and form values", async () => {
  const processUploadedRows = loadProcessUploadedRows();
  const rows = [
    { filename: "AST990001.png", title: "First title", keywords: "First, Alpha" },
    { filename: "AST990002.png", title: "Second title", keywords: "Second, Beta" },
  ];
  const tileOrder = [rows[1], rows[0]];
  let selected = 0;
  const configured = [];
  const page = {
    locator(selector) {
      if (selector === ".upload-tile__wrapper") {
        return {
          async count() { return tileOrder.length; },
          nth(index) {
            return { async click() { selected = index; } };
          },
        };
      }
      if (selector === "body") {
        return {
          async innerText() {
            return `File ID(s): 1 - Original name(s): ${tileOrder[selected].filename}\nActions:`;
          },
        };
      }
      if (selector === 'textarea[name="title"]') {
        return { async inputValue() { return tileOrder[selected].title; } };
      }
      if (selector === 'textarea[name="keywordsUITextArea"]') {
        return { async inputValue() { return tileOrder[selected].keywords.toLowerCase(); } };
      }
      throw new Error(`Unexpected selector: ${selector}`);
    },
    async waitForTimeout() {},
  };

  const verified = await processUploadedRows(
    page,
    rows,
    async (row) => configured.push(row.filename),
  );

  assert.deepEqual(verified.sort(), rows.map((row) => row.filename).sort());
  assert.deepEqual(configured, tileOrder.map((row) => row.filename));
});


test("reads the selected file type value instead of dropdown option text", async () => {
  const setAdobeFileType = loadSetAdobeFileType();
  let fileTypeButtonClicked = false;
  let selectedValue = "1";
  const button = {
    async count() { return 1; },
    async click() { fileTypeButtonClicked = true; },
    filter() { return this; },
    first() { return this; },
  };
  const page = {
    locator(selector) {
      if (selector === "body") {
        return {
          async innerText() {
            return "File type\nFile type\nIllustrations\nPhotos\nPhotos";
          },
        };
      }
      if (selector === 'select[name="contentType"]') {
        return {
          async count() { return 1; },
          async inputValue() { return selectedValue; },
        };
      }
      throw new Error(`Unexpected selector: ${selector}`);
    },
    getByRole(role) {
      if (role === "button") return button;
      if (role === "option") return { async click() { selectedValue = "2"; } };
      throw new Error(`Unexpected role: ${role}`);
    },
    async waitForTimeout() {},
  };
  const logger = { write() {}, note() {} };

  await setAdobeFileType(page, "illustrations", logger);

  assert.equal(fileTypeButtonClicked, true);
});


test("splits CDP uploads below the transfer-size limit", () => {
  const chunkFilesBySize = loadChunkFilesBySize();
  const files = ["one.png", "two.png", "three.png", "four.png"];
  const sizes = new Map([
    ["one.png", 13_000_000],
    ["two.png", 13_000_000],
    ["three.png", 13_000_000],
    ["four.png", 14_000_000],
  ]);

  const chunks = chunkFilesBySize(files, 45_000_000, (file) => sizes.get(file));

  assert.deepEqual(chunks, [
    ["one.png", "two.png", "three.png"],
    ["four.png"],
  ]);
});


test("rejects a single CDP file above the safe transfer size", () => {
  const chunkFilesBySize = loadChunkFilesBySize();

  assert.throws(
    () => chunkFilesBySize(["huge.png"], 45_000_000, () => 50_000_000),
    /too large/i,
  );
});


test("treats evidence screenshot failures as non-fatal", async () => {
  const screenshot = loadScreenshot();
  const notes = [];
  let screenshotOptions;
  const page = {
    async screenshot(options) {
      screenshotOptions = options;
      throw new Error("screenshot timed out");
    },
  };

  const result = await screenshot(page, "run-dir", "after_upload", {
    note(message) { notes.push(message); },
  });

  assert.equal(result, null);
  assert.equal(screenshotOptions.timeout, 15000);
  assert.match(notes[0], /screenshot.*after_upload.*timed out/i);
});


test("resumes image upload after an explicitly verified prefix", () => {
  const resumeImagePaths = loadResumeImagePaths();
  const images = ["AST000034.png", "AST000035.png", "AST000036.png", "AST000037.png"];

  assert.deepEqual(resumeImagePaths(images, 3), ["AST000037.png"]);
  assert.throws(() => resumeImagePaths(images, 5), /cannot exceed/i);
});

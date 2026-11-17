import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { managedDraftUpload } from "../tools/adobe_managed_upload.mjs";
import { createAdobeDraftAdapter } from "../tools/adobe_draft_adapter.mjs";

function paginatedAdapter(drafts, total = drafts.flat().length) {
  let current = 1;
  let target;
  let selected;
  let details;
  const thumbnail = item => item ? `https://example.test/240_F_${item.id}_thumbnail.jpg` : "";
  const page = {
    url: () => "https://contributor.stock.adobe.com/en/uploads",
    waitForTimeout: async () => assert.fail("draft selection must wait for matching details"),
    async waitForFunction(probe, index) {
      const tile = {
        closest: () => selected?.index === index ? {} : null,
        querySelector: () => ({ getAttribute: () => thumbnail(selected) }),
      };
      const oldDocument = globalThis.document;
      globalThis.document = {
        querySelectorAll: () => ({ [index]: tile }),
        querySelector: selector => {
          if (selector.includes("asset-sidebar-header-thumbnail")) {
            return { getAttribute: () => thumbnail(selected) };
          }
          assert.match(selector, /asset-sidebar-footer-file-ids/);
          return { innerText: details ? `File ID(s): ${details.id}\nOriginal name(s): ${details.filename}` : "" };
        },
      };
      try {
        if (details?.id !== selected?.id) {
          assert.equal(probe(index), false, "stale filename must not pass after the header changes");
        }
        details = selected;
        const filename = probe(index);
        return { jsonValue: async () => filename, dispose: async () => {} };
      } finally {
        globalThis.document = oldDocument;
      }
    },
    locator(selector) {
      if (selector === "body") {
        return { innerText: async () => `File types: All (${total})` };
      }
      if (selector === ".upload-tile__wrapper") {
        return { count: async () => drafts[current - 1].length,
          nth: index => ({ click: async () => {
            selected = { index, id: current * 100 + index, filename: drafts[current - 1][index] };
          } }) };
      }
      assert.equal(selector, ".the-paginator-list");
      return {
        count: async () => Number(drafts.length > 1),
        getByText: label => ({ count: async () => Number(label === "Next" && current < drafts.length) }),
        locator(child) {
          if (child === ".paginator-goto-input") {
            return { fill: async value => { target = Number(value); }, press: async () => { current = target; } };
          }
          assert.equal(child, "a.blue");
          return { getByText: label => ({
            count: async () => Number(current === Number(label)),
            waitFor: async () => assert.equal(current, Number(label)),
          }) };
        },
      };
    },
  };
  const adapter = createAdobeDraftAdapter({ page, options: { timeout: 2000 }, logger: { write() {} } });
  return { adapter, page };
}

test("Adobe inventory finds exact filenames on later pages and ignores unrelated duplicate names", async () => {
  const { adapter } = paginatedAdapter([["duplicate.jpg", "preview.jpg"], ["duplicate.jpg", "AST990103.png"]]);
  assert.deepEqual(await adapter.inventory(new Set(["AST990103.png"])),
    new Map([["AST990103.png", { pageNumber: 2, index: 1 }]]));
});

test("Adobe inventory rejects ambiguous targets and an incomplete page count", async () => {
  const { adapter: duplicate } = paginatedAdapter([["AST990103.png"], ["AST990103.png"]]);
  await assert.rejects(duplicate.inventory(new Set(["AST990103.png"])), /duplicate original filename/);
  const { adapter: incomplete } = paginatedAdapter([["different.jpg"], ["AST990103.png"]], 3);
  await assert.rejects(incomplete.inventory(new Set(["AST990103.png"])), /read 2 of 3 drafts/);
});

test("saved category uses Adobe's label, independent of its internal option ID", async () => {
  const row = { filename: "AST990088.png", title: "Title", keywords: "one,two", category: "8" };
  const { adapter, page } = paginatedAdapter([[row.filename]]);
  const originalLocator = page.locator;
  let label = "Graphic Resources";
  let reloaded = false;
  let tilesReady = false;
  page.reload = async () => { reloaded = true; tilesReady = false; };
  page.waitForLoadState = async () => assert.fail("saved draft verification must not wait for networkidle");
  page.getByText = () => ({ first: () => ({ waitFor: async () => {} }) });
  page.getByRole = role => ({ count: async () => role === "checkbox" ? 1 : 0, isChecked: async () => true });
  page.locator = selector => {
    if (selector === ".upload-tile__wrapper") {
      const tiles = originalLocator(selector);
      return {
        ...tiles,
        first: () => ({ waitFor: async () => { tilesReady = true; } }),
        nth: index => {
          assert.ok(!reloaded || tilesReady, "wait for the reloaded grid before selecting a draft");
          return tiles.nth(index);
        },
      };
    }
    if (selector.includes('select[name="category"]')) {
      return { count: async () => 1, inputValue: async () => "10432",
        evaluate: async inspect => inspect({ selectedOptions: [{ label }] }) };
    }
    if (selector.includes("contentType")) return { inputValue: async () => "2" };
    if (selector.includes("title")) return { inputValue: async () => row.title };
    if (selector.includes("keywords")) return { inputValue: async () => row.keywords };
    return originalLocator(selector);
  };
  await adapter.verifySaved(row);
  label = "Business";
  await assert.rejects(adapter.verifySaved(row), /category differs.*Graphic Resources.*Business/);
});

test("batch adapter records attempts before selecting multiple images and waits for every slot", async () => {
  const actions = [];
  const files = ["AST990102.png", "AST990103.png"];
  const page = {
    url: () => "https://contributor.stock.adobe.com/en/uploads",
    locator: () => ({ innerText: async () => "File types: All (233)" }),
    getByRole: () => ({ count: async () => 1, first: () => ({ click: async () => {} }) }),
    getByText: () => ({ waitFor: async () => {} }),
  };
  const adapter = createAdobeDraftAdapter({
    page, options: { timeout: 2000 }, logger: { write() {} },
    clickFirstVisible: async () => {},
    setFilesFromChooser: async (_page, click, selected) => {
      await click();
      actions.push(selected);
    },
    waitForNoUploadRemaining: async (_page, _timeout, before, expected) => {
      actions.push([before, expected]);
    },
  });
  await adapter.upload(files, () => actions.push("attempts"));
  assert.deepEqual(actions, ["attempts", files, [233, 2]]);
});

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stock-upload-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const receipts = path.join(dir, "receipts");
  const rows = ["AST000001.png", "AST000002.png"].map(filename => ({
    filename, title: "Title", keywords: "one,two", category: "8", releases: "",
  }));
  const files = rows.map(row => {
    const file = path.join(dir, row.filename);
    fs.writeFileSync(file, row.filename);
    return file;
  });
  const inventory = new Set();
  const uploads = [];
  const groups = [];
  const imported = [];
  const verified = new Set();
  const adobe = {
    async inventory() { return inventory; },
    async upload(group, checkpoint) {
      checkpoint();
      const filenames = group.map(file => path.basename(file));
      groups.push(filenames);
      for (const filename of filenames) {
        assert.ok(fs.existsSync(path.join(receipts, filename + ".attempt.json")));
        uploads.push(filename);
        inventory.add(filename);
      }
    },
    async importCsv(pending) { imported.push(pending.map(row => row.filename)); },
    async verifySaved(row) {
      if (!verified.has(row.filename)) throw new Error("Metadata not verified");
    },
    async saveAndVerify(row) { verified.add(row.filename); },
    confirmed() {},
  };
  return { rows, files, receipts, inventory, uploads, groups, imported, verified, adobe };
}

test("resume reconciles partial images and saved drafts by name, not prefix", async t => {
  const f = fixture(t);
  const realSave = f.adobe.saveAndVerify;
  f.adobe.saveAndVerify = async row => {
    if (row === f.rows[1]) throw new Error("Adobe page changed after one verified save");
    await realSave(row);
  };
  await assert.rejects(managedDraftUpload(f.rows, f.files, f.receipts, f.adobe), /page changed/);
  assert.ok(fs.existsSync(path.join(f.receipts, f.rows[0].filename + ".saved.json")));
  assert.ok(!fs.existsSync(path.join(f.receipts, f.rows[1].filename + ".saved.json")));
  f.adobe.saveAndVerify = realSave;
  await managedDraftUpload(f.rows, f.files, f.receipts, f.adobe);
  assert.deepEqual(f.uploads, f.rows.map(row => row.filename));
  assert.deepEqual(f.groups, [f.rows.map(row => row.filename)]);
  assert.deepEqual(f.imported[1], [f.rows[1].filename]);
  await managedDraftUpload(f.rows, f.files, f.receipts, f.adobe);
  assert.equal(f.imported.length, 2);
});

test("missing attempted file stops rather than uploading again", async t => {
  const f = fixture(t);
  const upload = f.adobe.upload;
  f.adobe.upload = async (group, checkpoint) => {
    checkpoint();
    f.inventory.add(path.basename(group[0]));
    throw new Error("Disconnected before Adobe confirmed receipt");
  };
  await assert.rejects(managedDraftUpload(f.rows, f.files, f.receipts, f.adobe), /Disconnected/);
  f.adobe.upload = upload;
  await assert.rejects(managedDraftUpload(f.rows, f.files, f.receipts, f.adobe), /Previous upload is not visible/);
  assert.equal(f.uploads.length, 0);
});

test("stop between upload groups leaves the next group without an attempt", async t => {
  const f = fixture(t);
  f.adobe.shouldStop = () => f.groups.length > 0;
  await assert.rejects(
    managedDraftUpload(f.rows, f.files, f.receipts, f.adobe, files => files.map(file => [file])),
    /stopped between image batches/,
  );
  assert.deepEqual(f.groups, [[f.rows[0].filename]]);
  assert.ok(fs.existsSync(path.join(f.receipts, f.rows[0].filename + ".attempt.json")));
  assert.ok(!fs.existsSync(path.join(f.receipts, f.rows[1].filename + ".attempt.json")));
});

test("adopts a pre-existing verified draft without sending its file or metadata again", async t => {
  const f = fixture(t);
  f.inventory.add(f.rows[1].filename);
  f.verified.add(f.rows[1].filename);
  await managedDraftUpload(f.rows, f.files, f.receipts, f.adobe);
  assert.deepEqual(f.uploads, [f.rows[0].filename]);
  assert.deepEqual(f.imported, [[f.rows[0].filename]]);
});

test("login failure before inventory does not create an upload attempt", async t => {
  const f = fixture(t);
  f.adobe.inventory = async () => { throw new Error("Adobe login required"); };
  await assert.rejects(managedDraftUpload(f.rows, f.files, f.receipts, f.adobe), /login required/);
  assert.deepEqual(fs.readdirSync(f.receipts), []);
});

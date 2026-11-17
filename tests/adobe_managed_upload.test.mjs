import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { managedDraftUpload } from "../tools/adobe_managed_upload.mjs";

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
  const imported = [];
  const verified = new Set();
  const adobe = {
    async inventory() { return inventory; },
    async upload(file, checkpoint) {
      const filename = path.basename(file);
      checkpoint();
      assert.ok(fs.existsSync(path.join(receipts, filename + ".attempt.json")));
      uploads.push(filename);
      inventory.add(filename);
    },
    async importCsv(pending) { imported.push(pending.map(row => row.filename)); },
    async verifySaved(row) {
      if (!verified.has(row.filename)) throw new Error("Metadata not verified");
    },
    async saveAndVerify(row) { verified.add(row.filename); },
    confirmed() {},
  };
  return { rows, files, receipts, inventory, uploads, imported, verified, adobe };
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
  assert.deepEqual(f.imported[1], [f.rows[1].filename]);
  await managedDraftUpload(f.rows, f.files, f.receipts, f.adobe);
  assert.equal(f.imported.length, 2);
});

test("missing attempted file stops rather than uploading again", async t => {
  const f = fixture(t);
  const upload = f.adobe.upload;
  f.adobe.upload = async (_file, checkpoint) => {
    checkpoint();
    throw new Error("Disconnected before Adobe confirmed receipt");
  };
  await assert.rejects(managedDraftUpload(f.rows, f.files, f.receipts, f.adobe), /Disconnected/);
  f.adobe.upload = upload;
  await assert.rejects(managedDraftUpload(f.rows, f.files, f.receipts, f.adobe), /Previous upload is not visible/);
  assert.equal(f.uploads.length, 0);
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

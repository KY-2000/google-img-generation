/** Managed upload state is per original filename, independent of batch membership. */
import fs from "node:fs";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

function durableJson(file, value) {
  const temporary = file + "." + randomUUID() + ".tmp";
  const fd = fs.openSync(temporary, "wx");
  try {
    fs.writeFileSync(fd, JSON.stringify(value) + "\n", "utf8");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  try {
    // An incomplete temp file is never interpreted as a receipt.
    fs.renameSync(temporary, file);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function readRecord(file) {
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : null;
}

function recordNewAttempt(file, value) {
  // Exclusive creation prevents another uploader from dispatching the same
  // file after taking an older snapshot of the grid.
  const fd = fs.openSync(file, "wx");
  try {
    fs.writeFileSync(fd, JSON.stringify(value) + "\n", "utf8");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
}

export async function managedDraftUpload(rows, imagePaths, receipts, adobe, splitUploads) {
  fs.mkdirSync(receipts, { recursive: true });
  const files = new Map(imagePaths.map(file => [path.basename(file), file]));
  const entries = rows.map(row => {
    if (!/^AST\d{6}\.png$/.test(row.filename) || !files.has(row.filename)) {
      throw new Error("Managed upload needs a valid AST PNG filename");
    }
    const image = files.get(row.filename);
    const identity = {
      filename: row.filename,
      image_sha256: createHash("sha256").update(fs.readFileSync(image)).digest("hex"),
      title: row.title, keywords: row.keywords, category: row.category,
    };
    const attemptFile = path.join(receipts, row.filename + ".attempt.json");
    const receiptFile = path.join(receipts, row.filename + ".saved.json");
    const attempted = readRecord(attemptFile);
    const saved = readRecord(receiptFile);
    for (const record of [attempted, saved].filter(Boolean)) {
      if (Object.keys(identity).some(key => record[key] !== identity[key])) {
        throw new Error("Local Adobe upload evidence conflicts for " + row.filename);
      }
    }
    if (saved && saved.status !== "saved_draft") throw new Error("Invalid saved draft receipt");
    return { row, image, identity, attemptFile, receiptFile, attempted, saved };
  });
  if (new Set(entries.map(item => item.row.filename)).size !== entries.length) {
    throw new Error("Duplicate filenames in managed upload");
  }
  const pending = entries.filter(item => !item.saved);
  if (!pending.length) return;
  const inventory = await adobe.inventory(new Set(pending.map(item => item.row.filename)));
  // Make the ambiguity check before sending any more files.
  for (const item of pending) {
    const filename = item.row.filename;
    if (item.attempted && !inventory.has(filename)) {
      throw new Error("Previous upload is not visible for " + filename +
        ". Check Adobe uploads and submission history before clearing its local attempt marker.");
    }
    if (!item.attempted && inventory.has(filename)) {
      // A pre-existing draft is safe to adopt only with saved metadata
      // and flags already matching; do not overwrite an unknown draft.
      await adobe.verifySaved(item.row);
      durableJson(item.receiptFile, { ...item.identity, status: "saved_draft", saved_at: new Date().toISOString() });
      item.saved = true;
      adobe.progress?.("draft_saved", filename, filename);
    }
  }
  const remaining = pending.filter(item => !item.saved);
  const unsent = new Map(remaining.filter(item => !inventory.has(item.row.filename)).map(item => [item.image, item]));
  const images = [...unsent.keys()];
  const chunks = images.length ? (splitUploads ? splitUploads(images) : [images]) : [];
  for (const chunk of chunks) {
    if (adobe.shouldStop?.()) throw new Error("Managed upload stopped between image batches");
    await adobe.upload(chunk, () => {
      for (const image of chunk) {
        const item = unsent.get(image);
        recordNewAttempt(item.attemptFile, { ...item.identity, attempted_at: new Date().toISOString() });
      }
    });
  }
  if (adobe.shouldStop?.()) throw new Error("Managed upload stopped before metadata import");
  if (remaining.length) {
    adobe.progress?.("importing", `${remaining.length} file(s)`);
    await adobe.importCsv(remaining.map(item => item.row));
  }
  for (const item of remaining) {
    if (adobe.shouldStop?.()) throw new Error("Managed upload stopped at a draft boundary");
    adobe.progress?.("saving", item.row.filename);
    await adobe.saveAndVerify(item.row);
    durableJson(item.receiptFile, { ...item.identity, status: "saved_draft", saved_at: new Date().toISOString() });
    adobe.confirmed(item.row.filename);
    adobe.progress?.("draft_saved", item.row.filename, item.row.filename);
  }
}

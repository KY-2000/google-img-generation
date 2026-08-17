import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  changedValidFolders,
  diffDirectorySnapshot,
  fallbackDownloaderButtonPoints,
  findValidXhsFolders,
  isPlatformWarningText,
  parseArgs,
  pickXhsPage,
  selectDownloadedFolders,
  shouldNavigateToBoard,
} from "../tools/xhs_download_playwright.mjs";

function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "xhs-download-playwright-"));
}

test("parseArgs requires board URL and download root", () => {
  assert.throws(() => parseArgs([]), /--board-url is required/);
  assert.throws(() => parseArgs(["--board-url", "https://example.com"]), /--download-root is required/);
});

test("parseArgs accepts CDP and note count", () => {
  const args = parseArgs([
    "--board-url",
    "https://www.xiaohongshu.com/board/test",
    "--download-root",
    "downloads",
    "--note-count",
    "3",
    "--cdp",
    "http://127.0.0.1:9222",
    "--auto-folder-permission",
    "--background",
    "--reset-folder-permission",
    "--folder-permission-timeout",
    "90",
    "--dry-run",
  ]);

  assert.equal(args.boardUrl, "https://www.xiaohongshu.com/board/test");
  assert.equal(args.noteCount, 3);
  assert.equal(args.cdp, "http://127.0.0.1:9222");
  assert.equal(args.autoFolderPermission, true);
  assert.equal(args.background, true);
  assert.equal(args.resetFolderPermission, true);
  assert.equal(args.folderPermissionTimeout, 90);
  assert.equal(args.dryRun, true);
  assert.equal(args.actionDelay, 5000);
  assert.equal(args.navigationCooldown, 10000);
});

test("parseArgs enforces conservative XHS pacing and batch limits", () => {
  const base = ["--board-url", "https://www.xiaohongshu.com/board/test", "--download-root", "downloads"];
  assert.throws(() => parseArgs([...base, "--note-count", "4"]), /limited to 3/);
  assert.throws(() => parseArgs([...base, "--action-delay", "4999"]), /at least 5000ms/);
  assert.throws(() => parseArgs([...base, "--navigation-cooldown", "9999"]), /at least 10000ms/);
});

test("platform warning detection recognizes warning and verification messages", () => {
  assert.equal(isPlatformWarningText("\u64cd\u4f5c\u9891\u7e41\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5"), true);
  assert.equal(isPlatformWarningText("Please verify you are human"), true);
  assert.equal(isPlatformWarningText("Downloading Notes 1 / 1"), false);
});

test("board navigation is skipped when the matching board is already open", () => {
  assert.equal(
    shouldNavigateToBoard(
      "https://www.xiaohongshu.com/board/abc?source=web_user_page",
      "https://www.xiaohongshu.com/board/abc?source=other",
    ),
    false,
  );
  assert.equal(
    shouldNavigateToBoard("https://www.xiaohongshu.com/explore", "https://www.xiaohongshu.com/board/abc"),
    true,
  );
});

test("reset folder permission requires the Windows permission helper", () => {
  assert.throws(
    () => parseArgs([
      "--board-url",
      "https://www.xiaohongshu.com/board/test",
      "--download-root",
      "downloads",
      "--reset-folder-permission",
    ]),
    /--reset-folder-permission requires --auto-folder-permission/,
  );
});

test("findValidXhsFolders returns direct children with metadata and images", () => {
  const root = makeTempDir();
  const valid = path.join(root, "valid");
  const noMetadata = path.join(root, "no-metadata");
  const noImage = path.join(root, "no-image");
  fs.mkdirSync(valid);
  fs.mkdirSync(noMetadata);
  fs.mkdirSync(noImage);
  fs.writeFileSync(path.join(valid, "metadata.json"), "{}");
  fs.writeFileSync(path.join(valid, "image_1.jpeg"), "jpeg");
  fs.writeFileSync(path.join(noMetadata, "image_1.jpeg"), "jpeg");
  fs.writeFileSync(path.join(noImage, "metadata.json"), "{}");

  assert.deepEqual(findValidXhsFolders(root), [valid]);
});

test("diffDirectorySnapshot prefers newly created valid folders", () => {
  const root = makeTempDir();
  const beforeExisting = path.join(root, "existing");
  const newValid = path.join(root, "new-valid");
  const newInvalid = path.join(root, "new-invalid");
  fs.mkdirSync(beforeExisting);
  const before = new Set(fs.readdirSync(root).map((name) => path.join(root, name)));

  fs.mkdirSync(newValid);
  fs.mkdirSync(newInvalid);
  fs.writeFileSync(path.join(newValid, "metadata.json"), "{}");
  fs.writeFileSync(path.join(newValid, "image_1.png"), "png");
  fs.writeFileSync(path.join(newInvalid, "metadata.json"), "{}");

  assert.deepEqual(diffDirectorySnapshot(root, before), [newValid]);
});

test("selectDownloadedFolders falls back to newest valid folder", async () => {
  const root = makeTempDir();
  const older = path.join(root, "older");
  const newer = path.join(root, "newer");
  for (const folder of [older, newer]) {
    fs.mkdirSync(folder);
    fs.writeFileSync(path.join(folder, "metadata.json"), "{}");
    fs.writeFileSync(path.join(folder, "image_1.png"), "png");
  }
  const olderTime = new Date(Date.now() - 60_000);
  fs.utimesSync(older, olderTime, olderTime);

  const selected = selectDownloadedFolders(root, new Set(), 1);

  assert.deepEqual(selected, [newer]);
});

test("changedValidFolders ignores unchanged valid folders and returns modified ones", async () => {
  const root = makeTempDir();
  const unchanged = path.join(root, "unchanged");
  const modified = path.join(root, "modified");
  for (const folder of [unchanged, modified]) {
    fs.mkdirSync(folder);
    fs.writeFileSync(path.join(folder, "metadata.json"), "{}");
    fs.writeFileSync(path.join(folder, "image_1.png"), "png");
  }

  const before = new Map([
    [unchanged, fs.statSync(unchanged).mtimeMs],
    [modified, fs.statSync(modified).mtimeMs - 5000],
  ]);

  assert.deepEqual(changedValidFolders(root, before), [modified]);
});

test("pickXhsPage prefers an already open matching board tab", async () => {
  const pages = [
    { url: () => "https://example.com/", marker: "first" },
    { url: () => "https://www.xiaohongshu.com/board/abc?source=web_user_page", marker: "board" },
    { url: () => "about:blank", marker: "blank" },
  ];
  const context = {
    pages: () => pages,
    newPage: async () => ({ marker: "new" }),
  };

  const picked = await pickXhsPage(context, "https://www.xiaohongshu.com/board/abc?source=web_user_page");

  assert.equal(picked.marker, "board");
});

test("pickXhsPage falls back to first non-helper page before creating a new page", async () => {
  const pages = [
    { url: () => "about:blank", marker: "helper" },
    { url: () => "https://www.xiaohongshu.com/explore", marker: "xhs" },
  ];
  const context = {
    pages: () => pages,
    newPage: async () => ({ marker: "new" }),
  };

  const picked = await pickXhsPage(context, "https://www.xiaohongshu.com/board/abc");

  assert.equal(picked.marker, "xhs");
});

test("fallbackDownloaderButtonPoints covers the lower-left floating book button", () => {
  const points = fallbackDownloaderButtonPoints({ width: 2048, height: 1024 });

  assert.deepEqual(points[0], { x: 56, y: 876 });
  assert(points.some((point) => point.x === 88 && point.y === 947));
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  parseArgs,
  parseSubmittedAt,
  isEligible,
  chunkFilesBySize,
} from "../tools/adobe_rejected_dreamstime_playwright.mjs";
import { openHistory } from "../tools/dreamstime_upload_history.mjs";

test("Dreamstime retry preserves the in-progress verification tab", async () => {
  const actions = [];
  const page = {
    getByRole: () => ({ filter: () => ({
      isVisible: async () => false,
      waitFor: async () => { throw new Error("timeout"); },
    }) }),
    goto: async () => actions.push("navigate"),
    reload: async () => actions.push("reload"),
    bringToFront: async () => actions.push("show"),
    locator: () => ({ innerText: async () => "Press & Hold to confirm you are a human" }),
  };
  await assert.rejects(openHistory(page, true), /human verification/);
  assert.deepEqual(actions, ["show"]);
});

test("Dreamstime retries read a ready tab before requesting a refresh", async () => {
  const actions = [];
  const page = {
    getByRole: (role) => {
      assert.equal(role, "table");
      return { filter: ({ hasText }) => {
        assert.equal(hasText, "ORIGINAL FILENAME");
        return {
          isVisible: async () => true,
          waitFor: async () => actions.push("ready"),
        };
      } };
    },
    goto: async () => actions.push("navigate"),
    reload: async () => actions.push("reload"),
  };
  await openHistory(page, true);
  await openHistory(page, true, true);
  await openHistory(page, false);
  assert.deepEqual(actions, ["ready", "reload", "ready", "navigate", "ready"]);
});

test("parses recent Adobe relative submission dates", () => {
  const now = new Date("2026-09-04T00:00:00Z");
  assert.equal(parseSubmittedAt("Submitted 11 days ago", now).toISOString(), "2026-08-24T00:00:00.000Z");
  assert.equal(isEligible(parseSubmittedAt("11 days ago", now), new Date("2026-08-21T00:00:00Z")), true);
  assert.equal(isEligible(parseSubmittedAt("20 days ago", now), new Date("2026-08-21T00:00:00Z")), false);
});

test("CDP upload chunks remain below transfer limit", () => {
  const sizes = new Map([["a.png", 30], ["b.png", 20], ["c.png", 10]]);
  assert.deepEqual(chunkFilesBySize(["a.png", "b.png", "c.png"], 45, (p) => sizes.get(p)), [["a.png"], ["b.png", "c.png"]]);
});

test("final commercial submission is opt-in", () => {
  const normal = parseArgs(["--cdp", "http://127.0.0.1:9222", "--scan-output", "scan.json"]);
  assert.equal(normal.submitCommercial, false);
  const submit = parseArgs(["--cdp", "http://127.0.0.1:9222", "--manifest", "m.json", "--submit-commercial"]);
  assert.equal(submit.submitCommercial, true);
});

test("explicit cutoff is parsed and retained", () => {
  const args = parseArgs(["--scan-output", "scan.json", "--since", "2026-08-21T00:00:00+08:00"]);
  assert.equal(args.since, "2026-08-21T00:00:00+08:00");
  assert.throws(() => parseArgs(["--scan-output", "scan.json", "--since", "not-a-date"]), /invalid --since/);
});

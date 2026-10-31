import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIR = join(dirname(fileURLToPath(import.meta.url)), "../../protocol/v1/fixtures/valid");

/** Load `protocol/v1/fixtures/valid/<name>.json`. */
export function fixture<T = Record<string, unknown>>(name: string): T {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8")) as T;
}

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

function normalizeStore(payload) {
  if (!payload || typeof payload !== "object") {
    return { globalBans: {} };
  }

  if (!payload.globalBans || typeof payload.globalBans !== "object") {
    return { globalBans: {} };
  }

  return { globalBans: payload.globalBans };
}

export class ModerationStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.data = { globalBans: {} };
    this.writeQueue = Promise.resolve();
  }

  async init() {
    await mkdir(path.dirname(this.filePath), { recursive: true });

    try {
      const raw = await readFile(this.filePath, "utf8");
      this.data = normalizeStore(JSON.parse(raw));
    } catch (error) {
      if (error.code === "ENOENT") {
        await this.save();
        return;
      }

      if (error instanceof SyntaxError) {
        throw new Error(
          `Could not parse ${this.filePath}. Remove or fix the JSON file and start again.`,
        );
      }

      throw error;
    }
  }

  getGlobalBan(userId) {
    return this.data.globalBans[userId] ?? null;
  }

  listGlobalBans() {
    return Object.entries(this.data.globalBans)
      .map(([userId, entry]) => ({ userId, ...entry }))
      .sort((left, right) => {
        return new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime();
      });
  }

  async setGlobalBan(userId, entry) {
    this.data.globalBans[userId] = entry;
    await this.save();
    return entry;
  }

  async removeGlobalBan(userId) {
    const existing = this.getGlobalBan(userId);

    if (!existing) {
      return null;
    }

    delete this.data.globalBans[userId];
    await this.save();
    return existing;
  }

  async save() {
    const snapshot = JSON.stringify(this.data, null, 2);

    this.writeQueue = this.writeQueue.then(async () => {
      const tempPath = `${this.filePath}.tmp`;
      await writeFile(tempPath, snapshot, "utf8");
      await rename(tempPath, this.filePath);
    });

    return this.writeQueue;
  }
}

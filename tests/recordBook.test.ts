/**
 * 盘口录制器纯逻辑测试：这批数据是当前唯一还在探索的方向，守的是数据资产本身——
 * 缺口必须被看见、清单必须能揭穿损坏/截断的文件、磁盘水位不能在阈值附近抖动。
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
// @ts-expect-error 脚本库是 ESM JS，无类型声明
import { DayStats, Reservoir, channelsForCoin, diskDecision, listUnverifiedDays, readManifest, verifyGzipFile, writeManifest } from "../scripts/book-lib.mjs";
import { makeTempDir } from "./support.js";

const DAY = "2026-09-04";
const T0 = Date.parse(`${DAY}T00:00:00Z`);

describe("Reservoir 分位数", () => {
  it("样本量内精确；超出容量后仍给出量级正确的分位数", () => {
    const r = new Reservoir(1000);
    for (let i = 1; i <= 1000; i++) r.push(i);
    expect(r.percentiles()).toEqual({ p50: 500, p90: 900, p99: 990 });
    const big = new Reservoir(2000);
    for (let i = 1; i <= 100_000; i++) big.push(i);
    const p = big.percentiles();
    expect(p.p50).toBeGreaterThan(40_000);
    expect(p.p50).toBeLessThan(60_000);
    expect(p.p99).toBeGreaterThan(95_000);
    expect(new Reservoir().percentiles()).toBeNull();
  });
});

describe("DayStats 缺口 / 覆盖 / 延迟", () => {
  it("静默超阈值只报一次；恢复时报缺口结束；覆盖秒数与最大间隔如实", () => {
    const s = new DayStats(DAY, { gapThresholdMs: 60_000 });
    const key = "BTC/l2Book";
    // 0s、1s、2s 三条消息，延迟 50ms
    for (const sec of [0, 1, 2]) expect(s.observe(key, T0 + sec * 1000, T0 + sec * 1000 - 50)).toBeNull();
    // 30s 后扫描：未超阈值
    expect(s.sweep(T0 + 32_000)).toEqual([]);
    // 90s 后扫描：进入缺口，只报一次
    expect(s.sweep(T0 + 92_000)).toEqual([{ key, silentMs: 90_000 }]);
    expect(s.sweep(T0 + 120_000)).toEqual([]);
    // 消息回来：缺口结束
    expect(s.observe(key, T0 + 150_000, T0 + 150_000 - 40)).toEqual({ gapEndedMs: 148_000 });
    s.dropped(key);
    const sum = s.summary(T0 + 151_000)[key];
    expect(sum).toMatchObject({ count: 4, gaps: 1, dropped: 1, max_gap_ms: 148_000, seconds_with_data: 4 });
    expect(sum.coverage).toBeCloseTo(4 / 86_400, 5);
    expect(sum.latency_ms.p50).toBe(50);
    // 从未收到消息的频道不算缺口
    const quiet = new DayStats(DAY);
    quiet.channel("ETH/trades");
    expect(quiet.sweep(T0 + 3_600_000)).toEqual([]);
    // 按 coin 归到文件名键
    expect(channelsForCoin(s.summary(), "BTC")).toHaveProperty("l2book");
    expect(channelsForCoin(s.summary(), "ETH")).toBeNull();
  });
});

describe("gzip 完整性与清单", () => {
  const gz = (text: string) => zlib.gzipSync(Buffer.from(text));

  it("多成员拼接计行正确；损坏与截断都判为 gzip_ok=false 且保留损坏点前的行数", async () => {
    const dir = makeTempDir();
    const a = gz('{"t":1}\n{"t":2}\n');
    const b = gz('{"t":3}\n');
    fs.writeFileSync(path.join(dir, "multi.gz"), Buffer.concat([a, b]));
    fs.writeFileSync(path.join(dir, "corrupt.gz"), Buffer.concat([a.subarray(0, a.length - 6), Buffer.from("xx")]));
    fs.writeFileSync(path.join(dir, "trunc.gz"), Buffer.concat([a, b.subarray(0, b.length - 8)]));

    const multi = await verifyGzipFile(path.join(dir, "multi.gz"));
    expect(multi).toMatchObject({ lines: 3, gzip_ok: true, error: null, bytes: a.length + b.length });
    expect(multi.sha256).toMatch(/^[0-9a-f]{64}$/);

    const corrupt = await verifyGzipFile(path.join(dir, "corrupt.gz"));
    expect(corrupt.gzip_ok).toBe(false);
    expect(corrupt.error).toBe("Z_DATA_ERROR");

    const trunc = await verifyGzipFile(path.join(dir, "trunc.gz"));
    expect(trunc.gzip_ok).toBe(false);
    expect(trunc.error).toBe("Z_BUF_ERROR");
    // 只缺 gzip 尾部（CRC+长度）：数据块已完整解出，三行都在，但文件被判为不完整
    expect(trunc.lines).toBe(3);
    expect(trunc.bytes).toBe(a.length + b.length - 8); // 原始字节按整文件重算
  });

  it("清单：缺清单的历史日被列出，写入后不再列出且内容可读", async () => {
    const root = makeTempDir();
    const day = path.join(root, "BTC", DAY);
    fs.mkdirSync(day, { recursive: true });
    fs.writeFileSync(path.join(day, "l2book.jsonl.gz"), gz('{"t":1}\n{"t":2}\n'));
    fs.writeFileSync(path.join(day, "trades.jsonl.gz"), gz('{"t":1}\n'));
    // 今天的目录不算历史
    fs.mkdirSync(path.join(root, "BTC", "2026-09-05"), { recursive: true });
    expect(listUnverifiedDays(root, "2026-09-05")).toEqual([{ coin: "BTC", date: DAY, dir: day }]);

    const m = await writeManifest(day, { coin: "BTC", date: DAY, channels: { l2book: { count: 2 } }, source: "test" });
    expect(m.files.l2book.lines).toBe(2);
    expect(m.files.trades.lines).toBe(1);
    expect(m.files.bbo).toBeUndefined();
    expect(readManifest(day)).toMatchObject({ version: 1, coin: "BTC", date: DAY, channels: { l2book: { count: 2 } } });
    expect(listUnverifiedDays(root, "2026-09-05")).toEqual([]);
  });
});

describe("磁盘水位判定（迟滞）", () => {
  it("超限即暂停；回落到 90% 且可用高出 3 个百分点才恢复", () => {
    const limits = { maxBytes: 20e9, minFreeRatio: 0.15 };
    const cases: Array<[name: string, suspended: boolean, bytes: number, free: number, expectSuspend: boolean]> = [
      ["正常", false, 5e9, 0.5, false],
      ["容量超限", false, 21e9, 0.5, true],
      ["可用不足", false, 5e9, 0.10, true],
      ["已暂停、容量刚低于上限但未到 90%", true, 19e9, 0.5, true],
      ["已暂停、容量回落但可用仍贴边", true, 10e9, 0.16, true],
      ["已暂停、两项都回落", true, 10e9, 0.30, false],
    ];
    for (const [name, suspended, bytes, free, expected] of cases) {
      const d = diskDecision(suspended, { bytes, freeRatio: free }, limits);
      expect(d.suspend, name).toBe(expected);
      expect(d.changed, name).toBe(expected !== suspended);
    }
    expect(diskDecision(false, { bytes: 21e9, freeRatio: 0.5 }, limits).reason).toMatch(/GB/);
  });
});

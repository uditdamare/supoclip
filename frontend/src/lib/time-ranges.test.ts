import { describe, expect, it } from "vitest";
import { formatClock, parseClockTime, planBatches, rangeText } from "./time-ranges";

describe("parseClockTime", () => {
  it.each([
    ["754", 754],
    ["12:34", 754],
    ["90:00", 5400],
    ["1:02:03", 3723],
    ["01:10:00", 4200],
    ["1h2m3s", 3723],
    ["1h 10m", 4200],
    [" 0:30 ", 30],
  ])("reads %s", (text, seconds) => {
    expect(parseClockTime(text)).toBe(seconds);
  });

  it.each(["", "abc", "1:75", "1:61:00", "-5", "1:2:3:4", "12:3x"])("rejects %s", (text) => {
    expect(parseClockTime(text)).toBeNull();
  });
});

describe("formatClock", () => {
  it("prints hours, minutes and seconds", () => {
    expect(formatClock(4210)).toBe("1:10:10");
    expect(formatClock(59.6)).toBe("0:01:00");
    expect(formatClock(-3)).toBe("0:00:00");
  });
});

describe("planBatches", () => {
  it("cuts a long stream into back-to-back batches", () => {
    const batches = planBatches(0, 10 * 3600, 70 * 60);
    expect(batches).toHaveLength(9);
    expect(batches[0]).toEqual({ start: 0, end: 4200 });
    expect(batches[1]).toEqual({ start: 4200, end: 8400 });
    expect(batches[8].end).toBe(36000);
    // No gaps and no overlap.
    batches.slice(1).forEach((batch, index) => expect(batch.start).toBe(batches[index].end));
  });

  it("honours a start and an end", () => {
    expect(planBatches(3600, 9000, 1800)).toEqual([
      { start: 3600, end: 5400 },
      { start: 5400, end: 7200 },
      { start: 7200, end: 9000 },
    ]);
  });

  it("drops a leftover that is too short to process", () => {
    expect(planBatches(0, 4200 + 10, 4200)).toEqual([{ start: 0, end: 4200 }]);
  });

  it("returns nothing for impossible input", () => {
    expect(planBatches(100, 100, 60)).toEqual([]);
    expect(planBatches(0, 100, 0)).toEqual([]);
  });
});

describe("rangeText", () => {
  it("is the string the API reads", () => {
    expect(rangeText({ start: 4210, end: 8400 })).toBe("1:10:10-2:20:00");
  });
});

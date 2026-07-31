import { describe, it, expect } from "vitest";
import { Semaphore } from "../../src/engine/semaphore.js";

describe("Semaphore", () => {
  it("allows acquire up to max", async () => {
    const sem = new Semaphore(3);
    await sem.acquire();
    await sem.acquire();
    await sem.acquire();
    expect(sem.acquired).toBe(3);
    expect(sem.queued).toBe(0);
  });

  it("queues excess acquirers", async () => {
    const sem = new Semaphore(2);
    await sem.acquire();
    await sem.acquire();

    // Third acquire should queue
    let resolved = false;
    const p = sem.acquire().then(() => {
      resolved = true;
    });
    expect(resolved).toBe(false);
    expect(sem.queued).toBe(1);

    // Release one slot
    sem.release();
    await p;
    expect(resolved).toBe(true);
  });

  it("maintains FIFO order", async () => {
    const sem = new Semaphore(1);
    await sem.acquire();

    const order: number[] = [];
    const p2 = sem.acquire().then(() => order.push(2));
    const p3 = sem.acquire().then(() => order.push(3));

    // Release in order
    sem.release(); // slot → p2
    await p2;
    sem.release(); // slot → p3
    await p3;

    expect(order).toEqual([2, 3]);
  });

  it("tracks max and queued correctly", () => {
    const sem = new Semaphore(5);
    expect(sem.maxSlots).toBe(5);
    expect(sem.acquired).toBe(0);
    expect(sem.queued).toBe(0);
  });
});

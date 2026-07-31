/**
 * LLM concurrency semaphore.
 *
 * Limits the number of concurrent LLM requests across all engine instances.
 * FIFO queue — first to acquire is first to be released.
 */

export class Semaphore {
  private current = 0;
  private readonly queue: Array<() => void> = [];

  constructor(private readonly max: number) {}

  /** Acquire a slot. Blocks if `max` slots are in use. */
  async acquire(): Promise<void> {
    if (this.current < this.max) {
      this.current++;
      return;
    }
    return new Promise<void>((resolve) => {
      this.queue.push(resolve);
    });
  }

  /** Release a slot. Unblocks the next waiter in FIFO order. */
  release(): void {
    this.current--;
    if (this.queue.length > 0) {
      this.current++;
      const next = this.queue.shift()!;
      next();
    }
  }

  /** Current number of acquired slots. */
  get acquired(): number {
    return this.current;
  }

  /** Number of waiters in the queue. */
  get queued(): number {
    return this.queue.length;
  }

  /** Maximum concurrent slots. */
  get maxSlots(): number {
    return this.max;
  }
}

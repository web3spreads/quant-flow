/**
 * 异步互斥锁：阻塞获取、带超时获取、非阻塞尝试三种用法。
 *
 * - tryAcquire()          等价 acquire(blocking=False)：拿不到立刻返回 false
 * - acquire(timeoutMs)    等价 acquire(timeout=N)：超时返回 false
 * - release()
 *
 * 引擎的互斥承诺不变：同一时刻只有一条周期（网格/限价单监控）在动账户，
 * 冲突时后来者跳过本轮。TS 单线程下锁保护的是「跨 await 的临界区」——一个
 * 周期内部有几十次 await，没有锁照样会被另一条循环插进来踩乱持仓与挂单状态。
 */
export class AsyncMutex {
  private locked = false;
  private waiters: Array<(ok: boolean) => void> = [];

  tryAcquire(): boolean {
    if (this.locked) return false;
    this.locked = true;
    return true;
  }

  async acquire(timeoutMs?: number): Promise<boolean> {
    if (!this.locked) {
      this.locked = true;
      return true;
    }
    return new Promise<boolean>((resolve) => {
      let settled = false;
      const waiter = (ok: boolean) => {
        if (settled) return;
        settled = true;
        if (timer !== undefined) clearTimeout(timer);
        resolve(ok);
      };
      const timer =
        timeoutMs !== undefined && timeoutMs >= 0
          ? setTimeout(() => {
              const idx = this.waiters.indexOf(waiter);
              if (idx >= 0) this.waiters.splice(idx, 1);
              waiter(false);
            }, timeoutMs)
          : undefined;
      this.waiters.push(waiter);
    });
  }

  release(): void {
    const next = this.waiters.shift();
    if (next) {
      // 锁直接移交给下一个等待者（保持 locked=true）
      next(true);
    } else {
      this.locked = false;
    }
  }

  get isLocked(): boolean {
    return this.locked;
  }
}

/** 可中断睡眠：停止信号触发时立即醒来，不必等满时长。 */
export function sleepAbortable(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted || ms <= 0) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    }
    signal?.addEventListener("abort", done, { once: true });
  });
}

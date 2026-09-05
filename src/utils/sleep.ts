/**
 * 可注入 sleep：生产真等，模拟器直接放行（历史回放不该被限流保护的 sleep 拖慢）。
 *
 * 只用于「礼貌性等待」（防限流、退避重试、等成交落库）。调度循环的可中断睡眠
 * 走 utils/mutex 的 sleepAbortable，不经此处——回测不使用调度循环。
 */

let sleepImpl: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms));

export function sleep(ms: number): Promise<void> {
  return sleepImpl(ms);
}

/** 注入 sleep 实现（模拟器用）；返回恢复函数 */
export function installSleep(impl: (ms: number) => Promise<void>): () => void {
  const prev = sleepImpl;
  sleepImpl = impl;
  return () => {
    sleepImpl = prev;
  };
}

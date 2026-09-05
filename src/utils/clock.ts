/**
 * 可注入时钟：生产走墙钟，回测/模拟把「现在」指向历史时刻。
 *
 * 交易主链路里所有「现在几点」（状态时间戳、屏障计时、保护插件的暂停期、
 * 成交确认的时间基准）都必须经由这里取值——直接 Date.now() 的代码在回测里
 * 会把历史成交的时序全部错位，屏障与冷却期也失去意义。
 */

let nowImpl: () => number = () => Date.now();

export const clock = {
  /** 当前毫秒时间戳 */
  now(): number {
    return nowImpl();
  },
  /** 当前秒时间戳（浮点） */
  nowSecs(): number {
    return nowImpl() / 1000;
  },
  /** 当前时刻的 Date 对象 */
  date(): Date {
    return new Date(nowImpl());
  },
  /** 注入时钟实现（模拟器用）；返回恢复函数 */
  install(impl: () => number): () => void {
    const prev = nowImpl;
    nowImpl = impl;
    return () => {
      nowImpl = prev;
    };
  },
  /** 恢复墙钟 */
  reset(): void {
    nowImpl = () => Date.now();
  },
};

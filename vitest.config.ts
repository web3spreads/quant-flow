import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    // 测试禁网络与真实密钥：全部交易所/LLM 行为由 tests/support.ts 的桩提供
    environment: "node",
    testTimeout: 15000,
  },
});

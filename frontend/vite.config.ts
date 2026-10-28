import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * dev proxy 的上游地址。
 *
 * 本仓库唯一的后端入口是 demo_main.py（FastAPI），
 * README 中的启动命令固定使用 8001 端口，
 * 因此这里的默认值与之一致 —— 开箱即用，无需设置环境变量。
 *
 * 若你把后端跑在其他端口，用环境变量覆盖即可：
 *     VITE_PROXY_TARGET=http://localhost:8000 npm run dev
 *
 * 说明：
 *   - demo_main.py 不连接数据库、缓存或任何行情数据源，
 *     启动过程零外部依赖。
 *   - 浏览器看到的都是同源请求（/api 前缀），
 *     因此后端无需配置 CORS。
 */
const proxyTarget =
  process.env.VITE_PROXY_TARGET ?? 'http://localhost:8001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    port: 5173,
    strictPort: false,
    /**
     * 通过 dev proxy 转发 /api 到后端。
     *
     * 这样浏览器看到的都是同源请求，
     * 因此后端无需配置 CORS。
     *
     * target 使用 localhost 而非 127.0.0.1：
     *   Vite dev server 默认监听 IPv6 的 [::1]，
     *   若proxy 写死 IPv4 的 127.0.0.1，
     *   在部分环境下会连不上上游（os error 10061）。
     *   localhost 可同时解析到 ::1 与 127.0.0.1，更稳妥。
     */
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
})

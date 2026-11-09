import { fileURLToPath, URL } from 'node:url'
import path from 'node:path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import ElementPlus from 'unplugin-element-plus/vite'
import { createSvgIconsPlugin } from 'vite-plugin-svg-icons'
import eceTsPlugin from './vite/ts-plugin.mjs'

/**
 * 课程原型前端：Vue 3 + Element Plus（基于 mall-admin-web 模板改造）。
 * 交付时构建为 dist/，由 FastAPI 在存在 dist 时优先服务 / 与 /assets/*。
 *
 * 说明：运行环境禁止 Node 子进程管道（esbuild spawn EPERM），故关闭 esbuild，
 * 由 ./vite/ts-plugin.mjs 用 TypeScript 编译器在进程内转译 TS；构建不压缩。
 */
export default defineConfig({
  base: '/',
  // 关闭 esbuild（改用进程内 TS 转译）
  esbuild: false,
  plugins: [
    eceTsPlugin(),
    vue(),
    // Element Plus 组件自动导入
    AutoImport({
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      resolvers: [ElementPlusResolver({ importStyle: 'sass' })],
    }),
    // 覆盖 Element Plus 默认主题色
    ElementPlus({
      useSource: true,
    }),
    // 模板自带 SVG 图标
    createSvgIconsPlugin({
      iconDirs: [path.resolve(process.cwd(), 'src/icons/svg')],
      symbolId: 'icon-[dir]-[name]',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        additionalData: `
          @use "@/styles/element/index.scss" as *;
          @use "@/styles/var.scss" as *;
        `,
        silenceDeprecations: ['legacy-js-api', 'if-function'],
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8777',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'esnext',
    minify: false,
    cssMinify: false,
    chunkSizeWarningLimit: 4000,
  },
})

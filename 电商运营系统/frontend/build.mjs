/**
 * 构建入口（沙箱兼容）：不使用 vite 配置文件加载器（它内部用 esbuild 子进程），
 * 而是直接调用 Vite JS API 传入内联配置；TS 由 ./vite/ts-plugin.mjs 在进程内转译。
 *
 * 用法（在 frontend/ 下）：
 *   node build.mjs
 * 产物：frontend/dist/（FastAPI 存在 dist 时优先服务）
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import ElementPlus from 'unplugin-element-plus/vite'
import { createSvgIconsPlugin } from 'vite-plugin-svg-icons'
import eceTsPlugin from './vite/ts-plugin.mjs'

const root = path.dirname(fileURLToPath(import.meta.url))

await build({
  configFile: false,
  root,
  base: '/',
  // 关闭 esbuild（改用进程内 TS 转译）
  esbuild: false,
  plugins: [
    eceTsPlugin(),
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver({ importStyle: 'sass' })] }),
    ElementPlus({ useSource: true }),
    createSvgIconsPlugin({
      iconDirs: [path.resolve(root, 'src/icons/svg')],
      symbolId: 'icon-[dir]-[name]',
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(root, 'src') },
  },
  // 显式注入编译期常量（Vite 构建默认由 esbuild define 完成，本环境需显式声明）
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    __VUE_OPTIONS_API__: 'true',
    __VUE_PROD_DEVTOOLS__: 'false',
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false',
    __VUE_I18N_FULL_INSTALL__: 'true',
    __VUE_I18N_LEGACY_API__: 'false',
    __INTLIFY_PROD_DEVTOOLS__: 'false',
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
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'esnext',
    minify: false,
    cssMinify: false,
    chunkSizeWarningLimit: 4000,
  },
  logLevel: 'info',
})

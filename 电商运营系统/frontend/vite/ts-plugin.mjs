/**
 * 沙箱兼容的 TypeScript 转译 + 编译期常量替换插件。
 *
 * 为什么需要它：
 * 1. 本机运行环境禁止 Node 子进程使用管道（esbuild 服务通过 child_process.spawn + pipe
 *    通信，会 EPERM），因此构建时设置 Vite `esbuild: false`，由本插件用 TypeScript
 *    编译器（纯 JS、进程内）转译 .ts 与 <script lang="ts">。
 * 2. `esbuild: false` 也会关闭 Vite 依赖 esbuild 的 define 插件，故这里同时完成
 *    `process.env.NODE_ENV` / `__VUE_OPTIONS_API__` 等编译期常量的字面量替换。
 */
import ts from 'typescript'

const TS_LIKE = /\.(ts|tsx|mts|cts)(\?|$)/

const DEFINES = {
  'process.env.NODE_ENV': JSON.stringify('production'),
  __VUE_OPTIONS_API__: 'true',
  __VUE_PROD_DEVTOOLS__: 'false',
  __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false',
  __VUE_I18N_FULL_INSTALL__: 'true',
  __VUE_I18N_LEGACY_API__: 'false',
  __INTLIFY_PROD_DEVTOOLS__: 'false',
}

function replaceDefines(code) {
  let out = code
  let changed = false
  for (const key of Object.keys(DEFINES)) {
    if (out.includes(key)) {
      out = out.split(key).join(DEFINES[key])
      changed = true
    }
  }
  return changed ? out : null
}

export default function eceTsPlugin() {
  return {
    name: 'ece-ts-transpile',
    enforce: 'pre',
    transform(code, id) {
      // 只处理 JS/TS 模块（含 .vue 的 script 子模块）；原始 .vue SFC 文本交给 plugin-vue
      const cleanId = id.split('?')[0]
      const isRawVue = cleanId.endsWith('.vue') && id === cleanId
      if (!cleanId || isRawVue || cleanId.endsWith('.d.ts')) return null
      const isVueTs = /\.vue\?.*\blang\.tsx?(\?|$)/.test(id)
      const isTs = TS_LIKE.test(cleanId) || isVueTs
      const isJs = /\.(js|mjs|cjs|jsx)(\?|$)/.test(cleanId)
      if (!isTs && !isJs) return null

      let out = replaceDefines(code)
      if (isTs) {
        const result = ts.transpileModule(out ?? code, {
          fileName: isVueTs || cleanId.endsWith('.tsx') ? 'module.tsx' : 'module.ts',
          reportDiagnostics: false,
          compilerOptions: {
            target: ts.ScriptTarget.ESNext,
            module: ts.ModuleKind.ESNext,
            moduleResolution: ts.ModuleResolutionKind.Bundler,
            jsx: ts.JsxEmit.Preserve,
            isolatedModules: true,
            verbatimModuleSyntax: false,
            sourceMap: true,
            useDefineForClassFields: true,
          },
        })
        return { code: result.outputText, map: result.sourceMapText }
      }
      if (out === null) return null
      return { code: out, map: null }
    },
  }
}

// ESLint 9 flat config。
//
// 背景：仓库此前在 package.json 里配了 `lint` 脚本却没有任何配置文件，
// `npm run lint` 一直直接报错。这里补上配置，且只使用 devDependencies 里**已有**的包
// （@typescript-eslint/{parser,eslint-plugin}、eslint-plugin-react-hooks、globals、@eslint/js），
// 不引入 typescript-eslint 元包或 react-refresh 插件，避免为了 lint 而扩依赖。
import js from '@eslint/js'
import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'

export default [
  { ignores: ['dist', 'node_modules', 'coverage', '**/*.config.js', '**/*.config.ts'] },
  js.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      'react-hooks': reactHooks,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // TS 自己管未定义符号，ESLint 的 no-undef 在 .tsx 上只会误报
      'no-undef': 'off',
      // base 版不懂 TS 的类型/值命名空间分离（如 `import type { Market }` 与
      // `export function Market()` 并存是合法的），换用 TS 版
      'no-redeclare': 'off',
      '@typescript-eslint/no-redeclare': 'error',
      // 允许以 _ 开头的未使用参数（回调签名占位）
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
]

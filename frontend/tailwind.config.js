/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 深色终端主题
        surface: {
          DEFAULT: "#0d1117",   // 主背景
          1: "#161b22",          // 卡片背景
          2: "#1c2128",          // 悬停态/次级面板
          3: "#22272e",          // 边框内侧
        },
        border: {
          DEFAULT: "#30363d",
          subtle: "#21262d",
        },
        fg: {
          DEFAULT: "#e6edf3",    // 主文字
          muted: "#8b949e",       // 次级文字
          subtle: "#6e7681",      // 提示文字
        },
        // 量化专用色彩语义
        up: "#3fb950",           // 上涨绿
        down: "#f85149",         // 下跌红
        flat: "#8b949e",         // 平盘灰
        accent: {
          DEFAULT: "#58a6ff",    // 主要强调蓝
          hover: "#79c0ff",
          subtle: "#1f6feb",
        },
        warn: "#d29922",
        success: "#3fb950",
        danger: "#f85149",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: {
        "2xs": ["0.65rem", "0.9rem"],
      },
      animation: {
        "fade-in":        "fadeIn 150ms ease-out",
        "slide-in":       "slideIn 200ms ease-out",
        "slide-in-right": "slideInRight 260ms cubic-bezier(0.16, 1, 0.3, 1)",
        pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        fadeIn: { from: { opacity: "0" }, to: { opacity: "1" } },
        slideIn: {
          from: { opacity: "0", transform: "translateY(4px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        slideInRight: {
          from: { opacity: "0", transform: "translateX(100%)" },
          to:   { opacity: "1", transform: "translateX(0)" },
        },
      },
    },
  },
  plugins: [],
}

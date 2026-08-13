/**
 * 一个 provider 都没配好时的引导。
 *
 * 刻意把「装 Ollama，无需密钥」放在最前面：本地模型是默认路径，
 * 不该让用户以为必须先去某家厂商申请 API key 才能用上 AI 功能。
 */
export function OllamaOnboarding() {
  return (
    <div className="card p-4 border border-[#388bfd]/40 bg-[#0d1b2a]/40 space-y-2">
      <h2 className="text-sm font-semibold text-[#58a6ff]">还没有可用的模型服务</h2>
      <p className="text-xs text-[#c9d1d9] leading-relaxed">
        安装 <strong>Ollama</strong> 后在下方「Ollama（本地）」卡片里填入访问地址即可开始，
        <strong>无需任何 API 密钥</strong>。模型在你自己的机器上运行，数据不出本地。
      </p>
      <ol className="text-xs text-[#8b949e] leading-relaxed list-decimal pl-5 space-y-0.5">
        <li>
          从{" "}
          <a
            href="https://ollama.com/download"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#58a6ff] hover:underline"
          >
            ollama.com/download
          </a>{" "}
          安装并启动
        </li>
        <li>
          拉一个模型：
          <code className="font-mono bg-[#161b22] px-1.5 py-0.5 rounded ml-1">
            ollama pull qwen2.5:14b
          </code>
        </li>
        <li>
          在下方卡片填入地址
          <code className="font-mono bg-[#161b22] px-1.5 py-0.5 rounded mx-1">
            http://localhost:11434/v1
          </code>
          与模型名，点「保存」→「测试连接」
        </li>
      </ol>
      <p className="text-[10px] text-[#6e7681]">
        也可以改用 OpenAI / DeepSeek / Moonshot / 通义千问 / Anthropic —— 那些需要填 API 密钥。
      </p>
    </div>
  )
}
